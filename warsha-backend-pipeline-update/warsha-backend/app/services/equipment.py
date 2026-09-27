from decimal import Decimal
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.models import Equipment, BOMLine, EquipmentDimension, Material, EquipmentCostLine, SheetStockItem, BarStockItem
from app.schemas.schemas import EquipmentCreate, BOMLineCreate, EquipmentTreeOut
from app.services.nesting import build_bar_cutting_plan, TOL


class EquipmentService:

    @staticmethod
    async def create_equipment(db: AsyncSession, data: EquipmentCreate) -> Equipment:
        from app.core.database import current_tenant_id as _ctid_eq
        from sqlalchemy import func as _func
        payload = data.model_dump()
        # نحقن tenant_id تلقائياً من context
        payload['tenant_id'] = _ctid_eq.get() or 1
        # توليد الكود تلقائياً إن لم يُذكر — لازم يتحسب لكل tenant على حدة
        # (uq_equipment_tenant_code هو القيد الفعلي)، غير كده أول معدة لعميل
        # جديد بتاخد كود غريب بمجرد وجود عملاء تانيين بعدد معدات أكبر.
        if not payload.get("code"):
            tid_for_code = payload["tenant_id"]
            count = await db.scalar(
                select(_func.count()).select_from(Equipment).where(Equipment.tenant_id == tid_for_code)
            )
            n = (count or 0) + 1
            while True:
                candidate = f"EQ-{n:04d}"
                exists = await db.scalar(
                    select(Equipment.id).where(Equipment.code == candidate, Equipment.tenant_id == tid_for_code)
                )
                if not exists:
                    break
                n += 1
            payload["code"] = candidate
        # default name_en to name_ar if empty
        if not payload.get("name_en"):
            payload["name_en"] = payload.get("name_ar", "")
        # نحسب الـ level تلقائياً من المعدة الأب
        if payload.get("parent_id"):
            parent = await db.get(Equipment, payload["parent_id"])
            if parent is None:
                raise ValueError("المعدة الأب غير موجودة")
            payload["level"] = (parent.level or 0) + 1
        else:
            payload["level"] = 0

        # محطة 23 (الروادماب الأصلي): لو مربوطة بفاتورة شراء رسمية، القيم
        # بتتحدد من الفاتورة نفسها — مش من إدخال المستخدم الحر — عشان
        # purchase_date/purchase_value يفضلوا مرجع حقيقي مش رقم من الهوا.
        if payload.get("purchase_invoice_id"):
            from app.models.models import PurchaseInvoice
            inv = await db.get(PurchaseInvoice, payload["purchase_invoice_id"])
            if not inv or inv.tenant_id != payload["tenant_id"]:
                raise ValueError("فاتورة الشراء المرتبطة غير موجودة")
            payload["purchase_date"] = inv.date
            payload["purchase_value"] = inv.total

        obj = Equipment(**payload)
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def list_root_equipment(db: AsyncSession):
        from app.core.database import current_tenant_id as _ctid_lre
        result = await db.execute(
            select(Equipment)
            .where(Equipment.parent_id.is_(None), Equipment.tenant_id == (_ctid_lre.get() or 1))
            .order_by(Equipment.id)
        )
        return result.scalars().all()

    @staticmethod
    async def get_all_trees(db: AsyncSession):
        roots = await EquipmentService.list_root_equipment(db)
        trees = []
        for root in roots:
            root_eq = await EquipmentService.get_equipment(db, root.id)
            if root_eq:
                trees.append(await EquipmentService._build_node(db, root_eq, 0))
        return trees

    @staticmethod
    async def get_equipment(db: AsyncSession, eq_id: int):
        from app.core.database import current_tenant_id as _ctid_ge
        result = await db.execute(
            select(Equipment)
            .options(
                selectinload(Equipment.bom_lines).selectinload(BOMLine.material),
                selectinload(Equipment.dimensions),
                selectinload(Equipment.cost_lines),
            )
            .where(Equipment.id == eq_id, Equipment.tenant_id == (_ctid_ge.get() or 1))
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _unique_code(db: AsyncSession, tid: int, base_code: str) -> str:
        candidate = base_code
        n = 2
        while await db.scalar(select(Equipment.id).where(Equipment.tenant_id == tid, Equipment.code == candidate)):
            candidate = f"{base_code}-{n}"
            n += 1
        return candidate

    @staticmethod
    async def _duplicate_subtree(db: AsyncSession, src: Equipment, tid: int, new_code: str,
                                  new_parent_id: int | None, new_name_ar: str | None = None) -> Equipment:
        new_eq = Equipment(
            tenant_id=tid, code=new_code,
            name_ar=new_name_ar or src.name_ar,
            name_en=src.name_en, description=src.description,
            parent_id=new_parent_id, category_id=src.category_id, level=src.level,
            weight_kg=src.weight_kg, is_active=True,
            calc_unit=src.calc_unit, base_qty=src.base_qty,
            length_min=src.length_min, length_max=src.length_max,
        )
        db.add(new_eq)
        await db.flush()

        bom_lines = await db.execute(select(BOMLine).where(BOMLine.equipment_id == src.id))
        for line in bom_lines.scalars():
            db.add(BOMLine(
                tenant_id=tid, equipment_id=new_eq.id, material_id=line.material_id,
                qty=line.qty, unit_cost=line.unit_cost, total_cost=line.total_cost,
                notes=line.notes,
                dim_length_cm=line.dim_length_cm, dim_width_cm=line.dim_width_cm,
                dim_count=line.dim_count, dim_area_m2=line.dim_area_m2,
                calc_weight_kg=line.calc_weight_kg, bar_length_cm=line.bar_length_cm,
                waste_g=line.waste_g, line_type=line.line_type,
                scale_dim_length=line.scale_dim_length,
            ))

        children = await db.execute(select(Equipment).where(Equipment.parent_id == src.id))
        for child in children.scalars():
            child_code = await EquipmentService._unique_code(db, tid, f"{new_code}-{child.code}")
            await EquipmentService._duplicate_subtree(db, child, tid, child_code, new_eq.id)

        return new_eq

    @staticmethod
    async def duplicate_equipment(db: AsyncSession, eq_id: int, new_code: str, new_name_ar: str | None = None) -> Equipment:
        """نسخ معدة مع كل فرعياتها (recursively) وبنود BOM الخاصة بكل مستوى، بكود جديد"""
        from app.core.database import current_tenant_id as _ctid_dup
        tid = _ctid_dup.get() or 1

        src = await db.get(Equipment, eq_id)
        if src is None or src.tenant_id != tid:
            raise ValueError("المعدة غير موجودة")

        exists = await db.scalar(select(Equipment.id).where(Equipment.tenant_id == tid, Equipment.code == new_code))
        if exists:
            raise ValueError(f"كود المعدة {new_code} مستخدم بالفعل — اختر كوداً مختلفاً")

        new_eq = await EquipmentService._duplicate_subtree(
            db, src, tid, new_code, src.parent_id,
            new_name_ar or f"{src.name_ar} (نسخة)",
        )

        await db.commit()
        return await EquipmentService.get_equipment(db, new_eq.id)

    @staticmethod
    async def get_direct_children(db: AsyncSession, parent_id: int):
        result = await db.execute(
            select(Equipment)
            .where(Equipment.parent_id == parent_id)
            .order_by(Equipment.id)
        )
        return result.scalars().all()

    @staticmethod
    async def get_full_tree(db: AsyncSession, eq_id: int):
        eq = await EquipmentService.get_equipment(db, eq_id)
        if not eq:
            return None
        return await EquipmentService._build_node(db, eq, 0)

    @staticmethod
    async def _build_node(db: AsyncSession, eq: Equipment, depth: int) -> dict:
        # نستخدم سعر الوحدة الحالي من المخزن (لا القيمة المخزَّنة وقت إضافة البند)
        # حتى يتحدث إجمالي تكلفة BOM فورًا عند تغيّر سعر مادة في المخزون
        bom_cost = Decimal("0")
        for line in (eq.bom_lines or []):
            live_uc = line.material.unit_cost if line.material and line.material.unit_cost else line.unit_cost
            bom_cost += Decimal(str(line.qty)) * Decimal(str(live_uc))

        children_result = await db.execute(
            select(Equipment)
            .where(Equipment.parent_id == eq.id)
            .order_by(Equipment.id)
        )
        children_rows = children_result.scalars().all()

        children_nodes = []
        for child in children_rows:
            child_eq = await EquipmentService.get_equipment(db, child.id)
            if child_eq:
                child_node = await EquipmentService._build_node(db, child_eq, depth + 1)
                children_nodes.append(child_node)
                bom_cost += Decimal(str(child_node.get("bom_total_cost") or 0))

        cost_lines_data = [
            {
                "id": cl.id,
                "cost_type": cl.cost_type,
                "description": cl.description,
                "cost_per_unit": float(cl.cost_per_unit),
            }
            for cl in (eq.cost_lines or [])
        ]

        return {
            "id": eq.id,
            "code": eq.code,
            "name_ar": eq.name_ar,
            "name_en": eq.name_en,
            "description": eq.description,
            "parent_id": eq.parent_id,
            "category_id": eq.category_id,
            "level": eq.level,
            "weight_kg": float(eq.weight_kg) if eq.weight_kg else None,
            "cad_drawing_no": eq.cad_drawing_no,
            "cad_file_path": eq.cad_file_path,
            "is_active": eq.is_active,
            "serial_number":  getattr(eq, "serial_number",  None),
            "equip_status":   getattr(eq, "equip_status",   "active"),
            "purchase_date":  str(eq.purchase_date)  if getattr(eq, "purchase_date",  None) else None,
            "purchase_value": float(eq.purchase_value) if getattr(eq, "purchase_value", None) else None,
            "calc_unit":      getattr(eq, "calc_unit", "piece"),
            "base_qty":       float(eq.base_qty) if getattr(eq, "base_qty", None) else 1.0,
            "length_min":     float(eq.length_min) if getattr(eq, "length_min", None) is not None else None,
            "length_max":     float(eq.length_max) if getattr(eq, "length_max", None) is not None else None,
            "created_at": eq.created_at.isoformat() if eq.created_at else None,
            "updated_at": eq.updated_at.isoformat() if eq.updated_at else None,
            "bom_total_cost": float(bom_cost),
            "dimensions": [{"id": d.id, "dim_key": d.dim_key, "dim_value": d.dim_value, "unit": d.unit}
                           for d in (eq.dimensions or [])],
            "cost_lines": cost_lines_data,
            "children": children_nodes,
        }

    @staticmethod
    async def add_bom_line(db: AsyncSession, data: BOMLineCreate) -> BOMLine:
        from app.core.database import current_tenant_id as _ctid_bom
        tid_bom = _ctid_bom.get() or 1
        payload = data.model_dump()
        payload['tenant_id'] = tid_bom
        equipment = await db.get(Equipment, data.equipment_id)
        if equipment is None or equipment.tenant_id != tid_bom:
            raise ValueError("المعدة غير موجودة — لا يمكن إضافة BOM")
        material = await db.get(Material, data.material_id)
        if material is None:
            raise ValueError("المادة غير موجودة")
        qty = data.qty
        waste_kg = (data.waste_g or Decimal("0")) / Decimal("1000")

        if material and data.dim_length_cm and data.dim_width_cm and material.density_kg_m2:
            count = Decimal(str(data.dim_count or 1))
            area = (data.dim_length_cm / 100) * (data.dim_width_cm / 100) * count
            qty = (area * material.density_kg_m2) + waste_kg
            payload["dim_area_m2"] = area
            payload["calc_weight_kg"] = qty

        if material and data.bar_length_cm and material.weight_per_meter_kg:
            count = Decimal(str(data.dim_count or 1))
            qty = (data.bar_length_cm / 100) * material.weight_per_meter_kg * count + waste_kg
            payload["calc_weight_kg"] = qty

        payload["qty"] = qty
        total = qty * data.unit_cost
        line = BOMLine(**payload, total_cost=total)
        db.add(line)
        await db.commit()
        await db.refresh(line)
        return line

    @staticmethod
    async def get_bom(db: AsyncSession, equipment_id: int):
        """يرجع كل بنود BOM للمعدة بما فيها بنود الفرعيات (recursive) — يُستخدم في مولد التقطيع."""
        return await EquipmentService._collect_bom_lines(db, equipment_id, set())

    @staticmethod
    async def _collect_bom_lines(db: AsyncSession, equipment_id: int, visited: set) -> list:
        """Recursively collect all BOM lines for equipment and all descendants."""
        if equipment_id in visited:
            raise ValueError(
                f"تم اكتشاف حلقة دائرية في BOM عند المعدة رقم {equipment_id} — "
                f"معدة فرعية تشير (مباشرة أو بشكل متداخل) إلى معدة أصل لها بالفعل"
            )
        visited.add(equipment_id)

        lines_result = await db.execute(
            select(BOMLine).options(
                selectinload(BOMLine.material),
                selectinload(BOMLine.equipment),
            )
            .where(BOMLine.equipment_id == equipment_id)
        )
        lines = lines_result.scalars().all()

        children_result = await db.execute(
            select(Equipment).where(Equipment.parent_id == equipment_id).order_by(Equipment.id)
        )
        children = children_result.scalars().all()
        for child in children:
            lines = lines + await EquipmentService._collect_bom_lines(db, child.id, visited)

        return lines

    @staticmethod
    async def _collect_cost_lines(db: AsyncSession, equipment_id: int, visited: set) -> list:
        """Recursively collect all fixed cost lines (labor/electricity/...) for equipment and all descendants."""
        if equipment_id in visited:
            return []
        visited.add(equipment_id)

        lines_result = await db.execute(
            select(EquipmentCostLine).where(EquipmentCostLine.equipment_id == equipment_id)
        )
        lines = list(lines_result.scalars().all())

        children_result = await db.execute(
            select(Equipment).where(Equipment.parent_id == equipment_id).order_by(Equipment.id)
        )
        children = children_result.scalars().all()
        for child in children:
            lines = lines + await EquipmentService._collect_cost_lines(db, child.id, visited)

        return lines

    @staticmethod
    def _scale_line_for_length(line, length: Decimal):
        """إعادة حساب سطر BOM متغير (line_type=='variable') بناءً على طول مختار L.

        الحقول المخزَّنة في البند المتغير تمثّل معدّلات "لكل وحدة طول واحدة":
        - dim_count: عدد القطع لكل متر (مثال: ريش البريمة)
        - bar_length_cm: طول السيخ/الماسورة لكل متر (سم/متر)
        - dim_length_cm: عند scale_dim_length=True يمثّل سم لكل متر (للهيكل/الغطاء)، وإلا بُعد ثابت للقطعة
        - dim_width_cm: بُعد ثابت دائماً
        """
        if getattr(line, "line_type", "fixed") != "variable" or length is None:
            return line

        from types import SimpleNamespace
        material = line.material
        waste_kg = (line.waste_g or Decimal("0")) / Decimal("1000")

        if line.dim_count is not None:
            dim_count_scaled = int(round(float(line.dim_count) * float(length)))
        else:
            dim_count_scaled = None

        dim_length_cm = line.dim_length_cm
        if line.scale_dim_length and dim_length_cm is not None:
            dim_length_cm = dim_length_cm * length

        bar_length_cm = line.bar_length_cm
        if bar_length_cm is not None:
            bar_length_cm = bar_length_cm * length

        qty = line.qty
        calc_weight_kg = line.calc_weight_kg
        dim_area_m2 = line.dim_area_m2
        count = Decimal(str(dim_count_scaled if dim_count_scaled is not None else 1))

        if material and dim_length_cm and line.dim_width_cm and material.density_kg_m2:
            area = (dim_length_cm / 100) * (line.dim_width_cm / 100) * count
            qty = (area * material.density_kg_m2) + waste_kg
            dim_area_m2 = area
            calc_weight_kg = qty
        elif material and bar_length_cm and material.weight_per_meter_kg:
            qty = (bar_length_cm / 100) * material.weight_per_meter_kg * count + waste_kg
            calc_weight_kg = qty
        else:
            qty = line.qty * length

        live_unit_cost = material.unit_cost if material and material.unit_cost else line.unit_cost
        return SimpleNamespace(
            id=line.id,
            equipment_id=line.equipment_id,
            material_id=line.material_id,
            equipment=line.equipment,
            material=line.material,
            qty=qty,
            unit_cost=live_unit_cost,
            total_cost=qty * live_unit_cost,
            dim_length_cm=dim_length_cm,
            dim_width_cm=line.dim_width_cm,
            dim_count=dim_count_scaled,
            dim_area_m2=dim_area_m2,
            calc_weight_kg=calc_weight_kg,
            bar_length_cm=bar_length_cm,
            waste_g=line.waste_g,
            line_type=line.line_type,
        )

    @staticmethod
    async def calculate_bom_cost(db: AsyncSession, equipment_id: int, quantity: Decimal = Decimal("1")) -> dict:
        """Return all BOM lines multiplied by quantity (parametric BOM)."""
        lines = await EquipmentService._collect_bom_lines(db, equipment_id, set())
        return await EquipmentService._aggregate_lines(db, equipment_id, lines, quantity)

    @staticmethod
    async def calculate_bom_cost_for_length(db: AsyncSession, equipment_id: int, length: Decimal,
                                             quantity: Decimal = Decimal("1")) -> dict:
        """حساب تكلفة BOM لمعدة مترية عند طول مختار L داخل [length_min, length_max]."""
        equipment = await db.get(Equipment, equipment_id)
        if equipment is None:
            raise ValueError("المعدة غير موجودة")
        if equipment.length_min is not None and length < equipment.length_min:
            raise ValueError(f"الطول خارج النطاق المسموح للصنف ({equipment.length_min} - {equipment.length_max})")
        if equipment.length_max is not None and length > equipment.length_max:
            raise ValueError(f"الطول خارج النطاق المسموح للصنف ({equipment.length_min} - {equipment.length_max})")

        lines = await EquipmentService._collect_bom_lines(db, equipment_id, set())
        scaled_lines = [EquipmentService._scale_line_for_length(line, length) for line in lines]
        result = await EquipmentService._aggregate_lines(db, equipment_id, scaled_lines, quantity)
        result["length"] = float(length)
        return result

    @staticmethod
    async def _aggregate_lines(db: AsyncSession, equipment_id: int, lines: list, quantity: Decimal = Decimal("1")) -> dict:
        breakdown = {"material_cost": 0.0, "fixed_cost": 0.0, "total_weight_kg": 0.0, "lines": [], "cost_lines": []}

        # تجميع المواد المتكررة (لكل معدة + نفس أبعاد القطعة، حتى لا تختلط قطع بمقاسات مختلفة من نفس المادة في بند واحد)
        aggregated: dict[tuple, dict] = {}
        for line in lines:
            key = (line.equipment_id, line.material_id, line.dim_length_cm, line.dim_width_cm, line.bar_length_cm)
            base_qty = float(line.qty)
            scaled_qty = base_qty * float(quantity)
            live_uc = float(line.material.unit_cost) if line.material and line.material.unit_cost else float(line.unit_cost)
            scaled_cost = base_qty * live_uc * float(quantity)

            if key in aggregated:
                aggregated[key]["qty"] += scaled_qty
                aggregated[key]["total_cost"] += scaled_cost
            else:
                aggregated[key] = {
                    "material_code": line.material.code,
                    "material_name": line.material.name_ar,
                    "qty": scaled_qty,
                    "unit": line.material.unit,
                    "unit_cost": live_uc,
                    "total_cost": scaled_cost,
                    "id": line.id,
                    "line_type": line.line_type,
                    "material_id": line.material_id,
                    "equipment_id": line.equipment_id,
                    "material": {
                        "id": line.material.id,
                        "code": line.material.code,
                        "name_ar": line.material.name_ar,
                        "unit": line.material.unit,
                        "unit_cost": float(line.material.unit_cost),
                        "material_kind": line.material.material_kind,
                        "is_plate": line.material.is_plate,
                        "density_kg_m2": float(line.material.density_kg_m2) if line.material.density_kg_m2 else None,
                        "weight_per_meter_kg": float(line.material.weight_per_meter_kg) if line.material.weight_per_meter_kg else None,
                    },
                    "dim_length_cm": float(line.dim_length_cm) if line.dim_length_cm else None,
                    "dim_width_cm": float(line.dim_width_cm) if line.dim_width_cm else None,
                    "dim_count": line.dim_count,
                    "dim_area_m2": float(line.dim_area_m2) if line.dim_area_m2 else None,
                    "calc_weight_kg": float(line.calc_weight_kg) * float(quantity) if line.calc_weight_kg else None,
                    "bar_length_cm": float(line.bar_length_cm) if line.bar_length_cm else None,
                    "waste_g": float(line.waste_g) if line.waste_g else None,
                    "equipment": {
                        "id": line.equipment.id,
                        "code": line.equipment.code,
                        "name_ar": line.equipment.name_ar,
                        "level": line.equipment.level,
                    } if line.equipment else None,
                }

        for mat_data in aggregated.values():
            breakdown["material_cost"] += mat_data["total_cost"]
            if mat_data["material"].get("material_kind", "general") != "consumable":
                breakdown["total_weight_kg"] += mat_data.get("calc_weight_kg") or 0
            breakdown["lines"].append(mat_data)

        # التكاليف الثابتة (عمالة/كهرباء/مستهلكات/تكاليف عامة) المعرّفة على المعدة وأبنائها
        cost_lines = await EquipmentService._collect_cost_lines(db, equipment_id, set())
        for cl in cost_lines:
            line_total = float(cl.cost_per_unit) * float(quantity)
            breakdown["fixed_cost"] += line_total
            breakdown["cost_lines"].append({
                "id": cl.id,
                "equipment_id": cl.equipment_id,
                "cost_type": cl.cost_type,
                "description": cl.description,
                "cost_per_unit": float(cl.cost_per_unit),
                "total_cost": line_total,
            })

        # ── ملخص المواد: إجمالي عدد الألواح/الأسياخ/القطع المطلوبة لكل مادة عبر الشجرة ──
        summary_agg: dict[int, dict] = {}
        pieces_by_material: dict[int, list] = {}
        for line in lines:
            mat = line.material
            s = summary_agg.setdefault(mat.id, {
                "material_id": mat.id,
                "material_code": mat.code,
                "material_name": mat.name_ar,
                "unit": mat.unit,
                "is_plate": mat.is_plate,
                "material_kind": mat.material_kind,
                "total_qty": 0.0,
                "total_area_m2": 0.0,
                "bulk_area_m2": 0.0,
                "total_bar_length_cm": 0.0,
                "plate_length_cm": float(mat.plate_length_cm) if mat.plate_length_cm else None,
                "plate_width_cm": float(mat.plate_width_cm) if mat.plate_width_cm else None,
                "bar_length_cm": float(mat.bar_length_cm) if mat.bar_length_cm else None,
                "density_kg_m2": float(mat.density_kg_m2) if mat.density_kg_m2 else None,
                "weight_per_meter_kg": float(mat.weight_per_meter_kg) if mat.weight_per_meter_kg else None,
            })
            s["total_qty"] += float(line.qty) * float(quantity)
            if line.dim_area_m2:
                s["total_area_m2"] += float(line.dim_area_m2) * float(quantity)
            elif mat.is_plate and mat.unit and ("م" in mat.unit or "m2" in mat.unit.lower() or "²" in mat.unit):
                # بند لوح مُدخل كمساحة إجمالية مباشرة (بدون تحديد أبعاد قطع) — يُحسب كجزء من المساحة الكلية المطلوبة
                s["bulk_area_m2"] += float(line.qty) * float(quantity)
            if line.bar_length_cm:
                s["total_bar_length_cm"] += float(line.bar_length_cm) * float(line.dim_count or 1) * float(quantity)

            piece_qty = max(1, round(float(line.dim_count or 1) * float(quantity)))
            if mat.is_plate and line.dim_length_cm and line.dim_width_cm:
                pieces_by_material.setdefault(mat.id, []).append({
                    "label": f"L{line.id}",
                    "width": float(line.dim_length_cm),
                    "height": float(line.dim_width_cm),
                    "qty": piece_qty,
                })
            elif mat.material_kind == "bar" and line.bar_length_cm:
                pieces_by_material.setdefault(mat.id, []).append({
                    "label": f"L{line.id}",
                    "length": float(line.bar_length_cm),
                    "qty": piece_qty,
                })

        # ── مساحة/طول النسالة المتاحة لكل مادة (تُطرح قبل حساب الألواح/الأسياخ المطلوبة) ──
        mat_ids = list(summary_agg.keys())
        remnant_sheet_area: dict[int, float] = {}
        remnant_bar_length: dict[int, float] = {}
        if mat_ids:
            sheet_rows = (await db.execute(
                select(SheetStockItem.material_id, func.sum(SheetStockItem.length_cm * SheetStockItem.width_cm * SheetStockItem.qty))
                .where(SheetStockItem.material_id.in_(mat_ids), SheetStockItem.source == "remnant")
                .group_by(SheetStockItem.material_id)
            )).all()
            remnant_sheet_area = {mid: float(total) / 10000 for mid, total in sheet_rows if total}

            bar_rows = (await db.execute(
                select(BarStockItem.material_id, func.sum(BarStockItem.length_cm * BarStockItem.qty))
                .where(BarStockItem.material_id.in_(mat_ids), BarStockItem.source == "remnant")
                .group_by(BarStockItem.material_id)
            )).all()
            remnant_bar_length = {mid: float(total) for mid, total in bar_rows if total}

        PRECISE_UTILIZATION_THRESHOLD = 0.95  # تحت 95% استخدام من الوحدة الأخيرة → نعرض الكمية الدقيقة بدل تقريبها لوحدة كاملة

        def _precise_fields(fraction_pct, unit_size, precise_unit):
            """لو الكسر الأخير من الوحدة مستخدم بأقل من threshold، نرجّع تفاصيل دقيقة (قيمة مطلوبة/متبقي)."""
            if fraction_pct is None or fraction_pct <= 0 or fraction_pct >= PRECISE_UTILIZATION_THRESHOLD * 100:
                return None
            required = unit_size * (fraction_pct / 100)
            return {
                "precise_value": round(required, 2),
                "unit_size": round(unit_size, 2),
                "remainder": round(unit_size - required, 2),
                "precise_unit": precise_unit,
            }

        materials_summary = []
        for s in summary_agg.values():
            mat_id = s["material_id"]
            pieces = pieces_by_material.get(mat_id)
            whole_count = None
            fraction_pct = None
            remnant_covered = None
            precise = None
            oversized_warning = None
            total_area = s["total_area_m2"] + s["bulk_area_m2"]
            if s["is_plate"] and s["plate_length_cm"] and s["plate_width_cm"] and total_area > 0:
                # عدد الألواح المطلوبة = (إجمالي المساحة المطلوبة - النسالة المتاحة) ÷ مساحة اللوح القياسي
                available_remnant = remnant_sheet_area.get(mat_id, 0.0)
                remnant_covered = round(min(available_remnant, total_area), 2)
                net_area = max(total_area - available_remnant, 0.0)
                standard_area = (s["plate_length_cm"] / 100) * (s["plate_width_cm"] / 100)
                sheets_needed = net_area / standard_area if standard_area else 0
                whole_count = int(sheets_needed)
                fraction_pct = round((sheets_needed - whole_count) * 100, 1)
                count = round(sheets_needed, 2)
                unit_label = "لوح"
                precise = _precise_fields(fraction_pct, standard_area, "م²")
                # تحقق أن كل قطعة بمفردها تتسع فعليًا في لوح قياسي واحد (بدون لحام)
                if pieces:
                    pl, pw = float(s["plate_length_cm"]), float(s["plate_width_cm"])
                    for p in pieces:
                        w, h = float(p.get("width", 0)), float(p.get("height", 0))
                        fits = (w <= pl + TOL and h <= pw + TOL) or (w <= pw + TOL and h <= pl + TOL)
                        if not fits:
                            oversized_warning = (
                                f"قطعة بمقاس {w:.0f}×{h:.0f} سم أكبر من اللوح القياسي "
                                f"({pl:.0f}×{pw:.0f} سم) — لا يمكن تقطيعها من لوح واحد، تحتاج لحام/توصيل"
                            )
                            break
            elif (s["is_plate"] and s["plate_length_cm"] and s["plate_width_cm"] and s["density_kg_m2"]
                  and total_area <= 0 and s["total_qty"] > 0):
                # بند مُدخل بالكيلو مباشرة (بدون أبعاد قطع) — نحوّل وزن اللوح القياسي
                # ونقسم إجمالي الكيلوهات المطلوبة عليه لمعرفة عدد الألواح
                standard_area = (s["plate_length_cm"] / 100) * (s["plate_width_cm"] / 100)
                weight_per_sheet_kg = standard_area * s["density_kg_m2"]
                available_remnant_kg = remnant_sheet_area.get(mat_id, 0.0) * s["density_kg_m2"]
                remnant_covered = round(min(available_remnant_kg, s["total_qty"]), 2)
                net_kg = max(s["total_qty"] - available_remnant_kg, 0.0)
                sheets_needed = net_kg / weight_per_sheet_kg if weight_per_sheet_kg else 0
                whole_count = int(sheets_needed)
                fraction_pct = round((sheets_needed - whole_count) * 100, 1)
                count = round(sheets_needed, 2)
                unit_label = "لوح"
                precise = _precise_fields(fraction_pct, weight_per_sheet_kg, "كجم")
            elif s["is_plate"] and total_area > 0:
                available_remnant = remnant_sheet_area.get(mat_id, 0.0)
                remnant_covered = round(min(available_remnant, total_area), 2)
                count = round(max(total_area - available_remnant, 0.0), 2)
                unit_label = "م²"
            elif s["material_kind"] == "bar" and s["bar_length_cm"] and pieces:
                bar_len = s["bar_length_cm"]
                too_long = [p for p in pieces if float(p.get("length", 0)) > bar_len + TOL]
                if too_long:
                    p = too_long[0]
                    oversized_warning = (
                        f"قطعة بطول {float(p['length']):.0f} سم أطول من السيخ القياسي "
                        f"({bar_len:.0f} سم) — لا يمكن تقطيعها من سيخ واحد، تحتاج لحام/توصيل"
                    )
                    count = round(s["total_bar_length_cm"] / bar_len, 2) if bar_len else 0
                    unit_label = "سيخ (تقديري)"
                else:
                    plan = build_bar_cutting_plan(bar_len, pieces)
                    total_bars = plan["total_bars"]
                    total_used_cm = sum(b["used_length"] for b in plan["bars"])
                    utilization_pct = round((total_used_cm / (total_bars * bar_len)) * 100, 1) if total_bars and bar_len else 0
                    count = total_bars
                    unit_label = "سيخ"
                    whole_count = total_bars - 1 if utilization_pct < PRECISE_UTILIZATION_THRESHOLD * 100 else total_bars
                    fraction_pct = utilization_pct if utilization_pct < PRECISE_UTILIZATION_THRESHOLD * 100 else None
                    if fraction_pct is not None and fraction_pct > 0:
                        precise = _precise_fields(fraction_pct, bar_len, "سم")
            elif s["material_kind"] == "bar" and s["bar_length_cm"] and s["total_bar_length_cm"] > 0:
                available_remnant = remnant_bar_length.get(mat_id, 0.0)
                remnant_covered = round(min(available_remnant, s["total_bar_length_cm"]), 1)
                net_length = max(s["total_bar_length_cm"] - available_remnant, 0.0)
                bars_needed = net_length / s["bar_length_cm"]
                whole_count = int(bars_needed)
                fraction_pct = round((bars_needed - whole_count) * 100, 1)
                count = round(bars_needed, 2)
                unit_label = "سيخ"
                precise = _precise_fields(fraction_pct, s["bar_length_cm"], "سم")
            elif s["material_kind"] == "bar" and s["total_bar_length_cm"] > 0:
                available_remnant = remnant_bar_length.get(mat_id, 0.0)
                remnant_covered = round(min(available_remnant, s["total_bar_length_cm"]), 1)
                count = round(max(s["total_bar_length_cm"] - available_remnant, 0.0) / 100, 2)
                unit_label = "متر"
            elif (s["material_kind"] == "bar" and s["bar_length_cm"] and s["weight_per_meter_kg"]
                  and s["total_bar_length_cm"] <= 0 and s["total_qty"] > 0):
                # بند مُدخل بالكيلو مباشرة (بدون تحديد عدد/طول القطع) — نحوّل وزن السيخ القياسي
                # ونقسم إجمالي الكيلوهات المطلوبة عليه لمعرفة عدد الأسياخ
                weight_per_bar_kg = (s["bar_length_cm"] / 100) * s["weight_per_meter_kg"]
                available_remnant_kg = (remnant_bar_length.get(mat_id, 0.0) / 100) * s["weight_per_meter_kg"]
                remnant_covered = round(min(available_remnant_kg, s["total_qty"]), 2)
                net_kg = max(s["total_qty"] - available_remnant_kg, 0.0)
                bars_needed = net_kg / weight_per_bar_kg if weight_per_bar_kg else 0
                whole_count = int(bars_needed)
                fraction_pct = round((bars_needed - whole_count) * 100, 1)
                count = round(bars_needed, 2)
                unit_label = "سيخ"
                precise = _precise_fields(fraction_pct, weight_per_bar_kg, "كجم")
            else:
                count = round(s["total_qty"], 2)
                unit_label = s["unit"]
            materials_summary.append({
                "material_id": s["material_id"],
                "material_code": s["material_code"],
                "material_name": s["material_name"],
                "count": count,
                "unit_label": unit_label,
                "whole_count": whole_count,
                "fraction_pct": fraction_pct,
                "remnant_covered": remnant_covered if remnant_covered and remnant_covered > 0 else None,
                "precise": precise,
                "oversized_warning": oversized_warning,
            })

        breakdown["materials_summary"] = materials_summary
        breakdown["total_cost"] = breakdown["material_cost"] + breakdown["fixed_cost"]
        return breakdown

    # ── EquipmentCostLine CRUD ──────────────────────────────────────────────────

    @staticmethod
    async def add_cost_line(db: AsyncSession, equipment_id: int, cost_type: str,
                            description: str, cost_per_unit: Decimal) -> EquipmentCostLine:
        from app.core.database import current_tenant_id as _ctid_acl
        equipment = await db.get(Equipment, equipment_id)
        if equipment is None or equipment.tenant_id != (_ctid_acl.get() or 1):
            raise ValueError("المعدة غير موجودة")
        line = EquipmentCostLine(
            equipment_id=equipment_id,
            tenant_id=equipment.tenant_id,
            cost_type=cost_type,
            description=description,
            cost_per_unit=cost_per_unit,
        )
        db.add(line)
        await db.commit()
        await db.refresh(line)
        return line

    @staticmethod
    async def list_cost_lines(db: AsyncSession, equipment_id: int) -> list:
        result = await db.execute(
            select(EquipmentCostLine)
            .where(EquipmentCostLine.equipment_id == equipment_id)
            .order_by(EquipmentCostLine.id)
        )
        return result.scalars().all()

    @staticmethod
    async def delete_cost_line(db: AsyncSession, cost_line_id: int) -> None:
        line = await db.get(EquipmentCostLine, cost_line_id)
        if line is None:
            raise ValueError("بند التكلفة غير موجود")
        await db.delete(line)
        await db.commit()

    @staticmethod
    async def add_dimension(db: AsyncSession, equipment_id: int,
                            dim_key: str, dim_value: str, unit: str):
        dim = EquipmentDimension(equipment_id=equipment_id, dim_key=dim_key,
                                 dim_value=dim_value, unit=unit)
        db.add(dim)
        await db.commit()
        await db.refresh(dim)
        return dim
