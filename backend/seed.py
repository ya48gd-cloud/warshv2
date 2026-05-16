"""
seed.py — بيانات تجريبية شاملة لورشة تصنيع معدات الأعلاف
يمسح الداتا القديمة كاملاً ويُدخل بيانات جديدة
Run: python seed.py
"""
import asyncio
import hashlib
from decimal import Decimal
from datetime import date, timedelta

from sqlalchemy import text
from app.core.database import AsyncSessionLocal, engine
from app.models.models import Base


# ── Utility ───────────────────────────────────────────────────
def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


async def wipe_all():
    """حذف كل الجداول وإعادة إنشاؤها."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("🗑  Wiped all tables — clean slate")


async def seed():
    async with AsyncSessionLocal() as db:
        from app.models.models import (
            User, MaterialCategory, Material, StockMovement,
            Equipment, EquipmentDimension, BOMLine,
            WorkOrder, CostLine,
            Worker, Attendance, PayrollRun, PayrollLine,
            Customer, CustomerOrder, Invoice, InvoiceLine, CustomerPayment,
            Quotation, QuotationLine,
        )

        # ══════════════════════════════════════════════════════
        # 1. USERS
        # ══════════════════════════════════════════════════════
        users = [
            User(username="admin",      password_hash=_hash("admin123"),      full_name="مدير النظام",      role="admin",      is_active=True),
            User(username="accountant", password_hash=_hash("accountant123"), full_name="أحمد المحاسب",    role="accountant", is_active=True),
            User(username="production", password_hash=_hash("production123"), full_name="محمد مشرف الإنتاج", role="production", is_active=True),
            User(username="viewer",     password_hash=_hash("viewer123"),     full_name="سارة المراقبة",   role="viewer",     is_active=True),
        ]
        db.add_all(users)
        await db.flush()

        # ══════════════════════════════════════════════════════
        # 2. MATERIAL CATEGORIES
        # ══════════════════════════════════════════════════════
        cats = [
            MaterialCategory(name_ar="فولاذ وحديد",       name_en="Steel & Iron"),
            MaterialCategory(name_ar="هيدروليك",           name_en="Hydraulics"),
            MaterialCategory(name_ar="كهرباء وتحكم",       name_en="Electrical & Control"),
            MaterialCategory(name_ar="مسامير وبراغي",      name_en="Fasteners"),
            MaterialCategory(name_ar="مواد استهلاكية",     name_en="Consumables"),
            MaterialCategory(name_ar="تروس وناقلات",       name_en="Gears & Drives"),
            MaterialCategory(name_ar="شاشات وتغذية",       name_en="Screens & Feeders"),
            MaterialCategory(name_ar="مواد خام مساعدة",    name_en="Raw Auxiliaries"),
        ]
        db.add_all(cats)
        await db.flush()
        steel, hydro, elec, fast, cons, gears, screens, raw = cats

        # ══════════════════════════════════════════════════════
        # 3. MATERIALS  (includes plate materials with density)
        # ══════════════════════════════════════════════════════
        mats = [
            # ── ألواح فولاذية (is_plate=True) ─────────────────
            Material(
                code="ST-37-6MM",  name_ar="لوح فولاذ ST-37 سماكة 6mm",
                name_en="Steel Plate ST-37 6mm", unit="kg",
                unit_cost=Decimal("16.00"), stock_qty=Decimal("3000.000"),
                reorder_level=Decimal("400"), category_id=steel.id,
                supplier="شركة الفولاذ المتحدة",
                is_plate=True, thickness_mm=Decimal("6"),
                density_kg_m2=Decimal("47.10"),   # 6mm × 7850 kg/m³ / 1000
            ),
            Material(
                code="ST-52-10MM", name_ar="لوح فولاذ ST-52 سماكة 10mm",
                name_en="Steel Plate ST-52 10mm", unit="kg",
                unit_cost=Decimal("18.50"), stock_qty=Decimal("4200.000"),
                reorder_level=Decimal("500"), category_id=steel.id,
                supplier="شركة الفولاذ المتحدة",
                is_plate=True, thickness_mm=Decimal("10"),
                density_kg_m2=Decimal("78.50"),   # 10mm × 7850
            ),
            Material(
                code="ST-37-3MM",  name_ar="لوح فولاذ ST-37 سماكة 3mm",
                name_en="Steel Plate ST-37 3mm", unit="kg",
                unit_cost=Decimal("15.00"), stock_qty=Decimal("1500.000"),
                reorder_level=Decimal("300"), category_id=steel.id,
                supplier="شركة الفولاذ المتحدة",
                is_plate=True, thickness_mm=Decimal("3"),
                density_kg_m2=Decimal("23.55"),   # 3mm × 7850
            ),
            Material(
                code="SS-304-2MM", name_ar="لوح استانلس ستيل 304 سماكة 2mm",
                name_en="Stainless Steel 304 2mm", unit="kg",
                unit_cost=Decimal("95.00"), stock_qty=Decimal("800.000"),
                reorder_level=Decimal("100"), category_id=steel.id,
                supplier="مورد الاستانلس",
                is_plate=True, thickness_mm=Decimal("2"),
                density_kg_m2=Decimal("15.76"),   # 2mm × 7880
            ),
            # ── حديد مقطعي وأنابيب ───────────────────────────
            Material(code="PIPE-60",    name_ar="أنبوب فولاذي 60mm",       name_en="Steel Pipe 60mm",    unit="m",    unit_cost=Decimal("85.00"),   stock_qty=Decimal("150.000"),  reorder_level=Decimal("20"),  category_id=steel.id, supplier="مصنع الأنابيب الوطني"),
            Material(code="PIPE-40",    name_ar="أنبوب فولاذي 40mm",       name_en="Steel Pipe 40mm",    unit="m",    unit_cost=Decimal("55.00"),   stock_qty=Decimal("200.000"),  reorder_level=Decimal("30"),  category_id=steel.id),
            Material(code="SQ-BAR-40",  name_ar="مقطع مربع 40×40mm",       name_en="Square Bar 40x40",   unit="m",    unit_cost=Decimal("55.00"),   stock_qty=Decimal("200.000"),  reorder_level=Decimal("30"),  category_id=steel.id, supplier="مصنع الحديد الوطني"),
            Material(code="ANGLE-50",   name_ar="زاوية حديدية 50×50mm",    name_en="Angle Iron 50x50",   unit="m",    unit_cost=Decimal("42.00"),   stock_qty=Decimal("320.000"),  reorder_level=Decimal("50"),  category_id=steel.id),
            Material(code="CH-100",     name_ar="قناة حديد C-100",          name_en="Channel C-100",      unit="m",    unit_cost=Decimal("120.00"),  stock_qty=Decimal("90.000"),   reorder_level=Decimal("15"),  category_id=steel.id),
            Material(code="ROD-20",     name_ar="سيخ دائري 20mm",           name_en="Round Bar 20mm",     unit="m",    unit_cost=Decimal("38.00"),   stock_qty=Decimal("250.000"),  reorder_level=Decimal("40"),  category_id=steel.id),
            # ── هيدروليك ─────────────────────────────────────
            Material(code="HYD-PUMP-11",name_ar="طلمبة هيدروليك 11KW",     name_en="Hydraulic Pump 11KW",unit="pcs",  unit_cost=Decimal("6500.00"), stock_qty=Decimal("5.000"),    reorder_level=Decimal("1"),   category_id=hydro.id, supplier="Parker Hannifin"),
            Material(code="HYD-CYL-80", name_ar="أسطوانة هيدروليك 80mm",   name_en="Hydraulic Cyl 80mm", unit="pcs",  unit_cost=Decimal("2800.00"), stock_qty=Decimal("8.000"),    reorder_level=Decimal("2"),   category_id=hydro.id, supplier="Parker Hannifin"),
            Material(code="HYD-OIL-46", name_ar="زيت هيدروليك VG46",       name_en="Hydraulic Oil VG46", unit="لتر",  unit_cost=Decimal("28.00"),   stock_qty=Decimal("500.000"),  reorder_level=Decimal("100"), category_id=hydro.id),
            Material(code="HYD-HOSE-1", name_ar="خرطوم هيدروليك 1 بوصة",  name_en="Hyd Hose 1\"",       unit="m",    unit_cost=Decimal("65.00"),   stock_qty=Decimal("80.000"),   reorder_level=Decimal("15"),  category_id=hydro.id),
            # ── كهرباء وتحكم ─────────────────────────────────
            Material(code="MOTOR-15KW", name_ar="موتور كهربائي 15KW",       name_en="Motor 15KW",         unit="pcs",  unit_cost=Decimal("7200.00"), stock_qty=Decimal("6.000"),    reorder_level=Decimal("1"),   category_id=elec.id,  supplier="Siemens Agent"),
            Material(code="MOTOR-7KW",  name_ar="موتور كهربائي 7.5KW",      name_en="Motor 7.5KW",        unit="pcs",  unit_cost=Decimal("4100.00"), stock_qty=Decimal("8.000"),    reorder_level=Decimal("2"),   category_id=elec.id,  supplier="Siemens Agent"),
            Material(code="MOTOR-3KW",  name_ar="موتور كهربائي 3KW",        name_en="Motor 3KW",          unit="pcs",  unit_cost=Decimal("2200.00"), stock_qty=Decimal("5.000"),    reorder_level=Decimal("1"),   category_id=elec.id),
            Material(code="INVERTER-15",name_ar="انفرتر تحكم 15KW",         name_en="VFD Inverter 15KW",  unit="pcs",  unit_cost=Decimal("5500.00"), stock_qty=Decimal("4.000"),    reorder_level=Decimal("1"),   category_id=elec.id,  supplier="Delta Electronics"),
            Material(code="PLC-S7",     name_ar="وحدة تحكم PLC Siemens S7", name_en="PLC Siemens S7",     unit="pcs",  unit_cost=Decimal("8500.00"), stock_qty=Decimal("3.000"),    reorder_level=Decimal("1"),   category_id=elec.id),
            Material(code="SENSOR-PROX",name_ar="حساس تقاربي M18",          name_en="Proximity Sensor M18",unit="pcs", unit_cost=Decimal("120.00"),  stock_qty=Decimal("30.000"),   reorder_level=Decimal("5"),   category_id=elec.id),
            # ── تروس وناقلات ─────────────────────────────────
            Material(code="GEARBOX-20", name_ar="علبة تروس نسبة 1:20",      name_en="Gearbox 1:20",       unit="pcs",  unit_cost=Decimal("3800.00"), stock_qty=Decimal("6.000"),    reorder_level=Decimal("1"),   category_id=gears.id),
            Material(code="GEARBOX-40", name_ar="علبة تروس نسبة 1:40",      name_en="Gearbox 1:40",       unit="pcs",  unit_cost=Decimal("4500.00"), stock_qty=Decimal("4.000"),    reorder_level=Decimal("1"),   category_id=gears.id),
            Material(code="BELT-B80",   name_ar="سير نقل حركة B-80",         name_en="V-Belt B80",         unit="pcs",  unit_cost=Decimal("45.00"),   stock_qty=Decimal("50.000"),   reorder_level=Decimal("10"),  category_id=gears.id),
            Material(code="COUPLING-65",name_ar="وصلة مرنة 65mm",            name_en="Flexible Coupling 65",unit="pcs", unit_cost=Decimal("380.00"),  stock_qty=Decimal("12.000"),   reorder_level=Decimal("2"),   category_id=gears.id),
            Material(code="BEARING-305",name_ar="بلي 6305",                  name_en="Bearing 6305",       unit="pcs",  unit_cost=Decimal("85.00"),   stock_qty=Decimal("40.000"),   reorder_level=Decimal("8"),   category_id=gears.id),
            # ── شاشات وتغذية ─────────────────────────────────
            Material(code="SCREEN-3MM", name_ar="شاشة تصفية 3mm",           name_en="Sieve Screen 3mm",   unit="pcs",  unit_cost=Decimal("1200.00"), stock_qty=Decimal("12.000"),   reorder_level=Decimal("3"),   category_id=screens.id, supplier="مصنع الشاشات"),
            Material(code="SCREEN-5MM", name_ar="شاشة تصفية 5mm",           name_en="Sieve Screen 5mm",   unit="pcs",  unit_cost=Decimal("1200.00"), stock_qty=Decimal("10.000"),   reorder_level=Decimal("3"),   category_id=screens.id, supplier="مصنع الشاشات"),
            Material(code="SCREW-FED",  name_ar="ملولب تغذية D200",          name_en="Screw Feeder D200",  unit="m",    unit_cost=Decimal("450.00"),  stock_qty=Decimal("15.000"),   reorder_level=Decimal("3"),   category_id=screens.id),
            # ── مسامير وبراغي ────────────────────────────────
            Material(code="BOLT-M16",   name_ar="مسمار M16 استانلس",        name_en="Bolt M16 SS",        unit="pcs",  unit_cost=Decimal("8.00"),    stock_qty=Decimal("2000.000"), reorder_level=Decimal("300"), category_id=fast.id),
            Material(code="BOLT-M12",   name_ar="مسمار M12 استانلس",        name_en="Bolt M12 SS",        unit="pcs",  unit_cost=Decimal("4.50"),    stock_qty=Decimal("3000.000"), reorder_level=Decimal("500"), category_id=fast.id),
            Material(code="ANCHOR-M20", name_ar="بسيمة تثبيت M20",          name_en="Anchor Bolt M20",    unit="pcs",  unit_cost=Decimal("15.00"),   stock_qty=Decimal("500.000"),  reorder_level=Decimal("100"), category_id=fast.id),
            # ── مواد استهلاكية ───────────────────────────────
            Material(code="WELD-ROD",   name_ar="سلك لحام E7018",            name_en="Welding Rod E7018",  unit="kg",   unit_cost=Decimal("35.00"),   stock_qty=Decimal("200.000"),  reorder_level=Decimal("40"),  category_id=cons.id),
            Material(code="WELD-MIG",   name_ar="سلك ميج 0.8mm",            name_en="MIG Wire 0.8mm",     unit="kg",   unit_cost=Decimal("55.00"),   stock_qty=Decimal("80.000"),   reorder_level=Decimal("15"),  category_id=cons.id),
            Material(code="GRIND-DISC", name_ar="قرص جلخ 180mm",            name_en="Grinding Disc 180",  unit="pcs",  unit_cost=Decimal("18.00"),   stock_qty=Decimal("200.000"),  reorder_level=Decimal("50"),  category_id=cons.id),
            Material(code="PAINT-GRAY", name_ar="دهان رمادي أنتي كوروجن",   name_en="Gray Anti-Corrosion",unit="kg",   unit_cost=Decimal("45.00"),   stock_qty=Decimal("150.000"),  reorder_level=Decimal("30"),  category_id=cons.id),
        ]
        db.add_all(mats)
        await db.flush()

        # Unpack key materials
        st37,st52,st37_3,ss304, pipe60,pipe40,sqbar,angle,ch100,rod20, \
        hpump,hcyl,hyd_oil,hyd_hose, \
        mot15,mot75,mot3,inv,plc,sensor, \
        gbox20,gbox40,belt,coupling,bearing, \
        scr3,scr5,screw_fed, \
        bolt16,bolt12,anchor, \
        weld_rod,weld_mig,grind_disc,paint = mats

        # ══════════════════════════════════════════════════════
        # 4. STOCK MOVEMENTS (opening + purchases)
        # ══════════════════════════════════════════════════════
        movements = [
            # ألواح فولاذ — وارد بالأبعاد (10 ألواح 100×300 سم كل لوح 47.1 كجم)
            StockMovement(material_id=st37.id,  movement_type="in", qty=Decimal("3000.000"),
                unit_cost=Decimal("16.00"), total_cost=Decimal("48000"),
                reference="PO-2024-001", movement_date=date(2024,1,5),
                plate_length_cm=Decimal("100"), plate_width_cm=Decimal("300"),
                plate_area_m2=Decimal("3.00"), plate_weight_kg=Decimal("141.30"),
                notes="مشتريات — شركة الفولاذ المتحدة"),
            StockMovement(material_id=st52.id,  movement_type="in", qty=Decimal("4200.000"),
                unit_cost=Decimal("18.50"), total_cost=Decimal("77700"),
                reference="PO-2024-002", movement_date=date(2024,1,5),
                plate_length_cm=Decimal("150"), plate_width_cm=Decimal("300"),
                plate_area_m2=Decimal("4.50"), plate_weight_kg=Decimal("353.25"),
                notes="مشتريات — شركة الفولاذ المتحدة"),
            StockMovement(material_id=st37_3.id, movement_type="in", qty=Decimal("1500.000"),
                unit_cost=Decimal("15.00"), total_cost=Decimal("22500"),
                reference="PO-2024-003", movement_date=date(2024,1,8),
                notes="مشتريات"),
            StockMovement(material_id=ss304.id, movement_type="in", qty=Decimal("800.000"),
                unit_cost=Decimal("95.00"), total_cost=Decimal("76000"),
                reference="PO-2024-004", movement_date=date(2024,1,10),
                notes="مشتريات"),
            # صرف للإنتاج
            StockMovement(material_id=st52.id,  movement_type="out", qty=Decimal("420.000"),
                unit_cost=Decimal("18.50"), total_cost=Decimal("7770"),
                reference="WO-2024-001", movement_date=date(2024,2,5),
                notes="الورشة — لجسم المطرقة WO-2024-001",
                withdrawal_unit="weight"),
            StockMovement(material_id=st37.id,  movement_type="out", qty=Decimal("220.000"),
                unit_cost=Decimal("16.00"), total_cost=Decimal("3520"),
                reference="WO-2024-001", movement_date=date(2024,2,8),
                notes="الورشة — هيكل رئيسي",
                withdrawal_unit="weight"),
            # مواد أخرى
            StockMovement(material_id=mot15.id, movement_type="in", qty=Decimal("6.000"),
                unit_cost=Decimal("7200.00"), total_cost=Decimal("43200"),
                reference="PO-2024-005", movement_date=date(2024,1,10), notes="مشتريات Siemens"),
            StockMovement(material_id=mot75.id, movement_type="in", qty=Decimal("8.000"),
                unit_cost=Decimal("4100.00"), total_cost=Decimal("32800"),
                reference="PO-2024-005", movement_date=date(2024,1,10), notes="مشتريات Siemens"),
            StockMovement(material_id=inv.id,   movement_type="in", qty=Decimal("4.000"),
                unit_cost=Decimal("5500.00"), total_cost=Decimal("22000"),
                reference="PO-2024-006", movement_date=date(2024,1,12), notes="مشتريات Delta"),
            StockMovement(material_id=gbox20.id,movement_type="in", qty=Decimal("6.000"),
                unit_cost=Decimal("3800.00"), total_cost=Decimal("22800"),
                reference="PO-2024-007", movement_date=date(2024,1,15), notes="مشتريات"),
            StockMovement(material_id=weld_rod.id,movement_type="in",qty=Decimal("200.000"),
                unit_cost=Decimal("35.00"),  total_cost=Decimal("7000"),
                reference="PO-2024-008", movement_date=date(2024,1,15), notes="مشتريات"),
        ]
        db.add_all(movements)
        await db.flush()

        # ══════════════════════════════════════════════════════
        # 5. EQUIPMENT TREE
        # ══════════════════════════════════════════════════════

        # ── FG-500 مجرشة أعلاف ───────────────────────────────
        fg = Equipment(code="FG-500",     name_ar="مجرشة أعلاف 5 طن/ساعة",   name_en="Feed Grinder 5T/h",  level=0, weight_kg=Decimal("2800"), cad_drawing_no="DWG-FG500-Rev2", description="مجرشة أعلاف مدمجة 5 طن/ساعة بموتور 15KW")
        db.add(fg); await db.flush()

        grd = Equipment(code="FG500-GRD", name_ar="وحدة الطحن",              name_en="Grinding Unit",      parent_id=fg.id, level=1, weight_kg=Decimal("1100"), cad_drawing_no="DWG-GRD-001")
        scr_eq = Equipment(code="FG500-SCR", name_ar="وحدة الغربلة",         name_en="Screening Unit",     parent_id=fg.id, level=1, weight_kg=Decimal("650"),  cad_drawing_no="DWG-SCR-001")
        fed = Equipment(code="FG500-FED", name_ar="وحدة التغذية",            name_en="Feeding Unit",       parent_id=fg.id, level=1, weight_kg=Decimal("420"),  cad_drawing_no="DWG-FED-001")
        drv = Equipment(code="FG500-DRV", name_ar="وحدة الإدارة والتشغيل",   name_en="Drive System",       parent_id=fg.id, level=1, weight_kg=Decimal("380"),  cad_drawing_no="DWG-DRV-001")
        frm = Equipment(code="FG500-FRM", name_ar="الهيكل والإطار",           name_en="Main Frame",         parent_id=fg.id, level=1, weight_kg=Decimal("250"),  cad_drawing_no="DWG-FRM-001")
        db.add_all([grd, scr_eq, fed, drv, frm]); await db.flush()

        hm = Equipment(code="FG500-GRD-HM", name_ar="مطرقة الطحن",           name_en="Hammer Mill",        parent_id=grd.id,    level=2, weight_kg=Decimal("480"), cad_drawing_no="DWG-HM-001")
        vs = Equipment(code="FG500-SCR-VS", name_ar="شاشة اهتزازية",          name_en="Vibro Screen",       parent_id=scr_eq.id, level=2, weight_kg=Decimal("280"), cad_drawing_no="DWG-VS-001")
        sf = Equipment(code="FG500-FED-SF", name_ar="ملولب تغذية",            name_en="Screw Feeder",       parent_id=fed.id,    level=2, weight_kg=Decimal("180"), cad_drawing_no="DWG-SF-001")
        db.add_all([hm, vs, sf]); await db.flush()

        # ── MX-200 خلاط أعلاف ────────────────────────────────
        mx = Equipment(code="MX-200",     name_ar="خلاط أعلاف أفقي 2 طن",   name_en="Feed Mixer 2T",      level=0, weight_kg=Decimal("1600"), cad_drawing_no="DWG-MX200-Rev1", description="خلاط أعلاف سعة 2 طن/دفعة")
        db.add(mx); await db.flush()

        mx_drm = Equipment(code="MX200-DRM", name_ar="اسطوانة الخلط",        name_en="Mixing Drum",        parent_id=mx.id, level=1, weight_kg=Decimal("800"))
        mx_drv = Equipment(code="MX200-DRV", name_ar="وحدة تشغيل الخلاط",   name_en="Mixer Drive",        parent_id=mx.id, level=1, weight_kg=Decimal("350"))
        mx_frm = Equipment(code="MX200-FRM", name_ar="هيكل الخلاط",          name_en="Mixer Frame",        parent_id=mx.id, level=1, weight_kg=Decimal("450"))
        db.add_all([mx_drm, mx_drv, mx_frm]); await db.flush()

        # ── مكبس هيدروليكي HP-50 (معدة ثالثة) ───────────────
        hp = Equipment(code="HP-50",      name_ar="مكبس هيدروليكي 50 طن",   name_en="Hydraulic Press 50T", level=0, weight_kg=Decimal("3200"), cad_drawing_no="DWG-HP50-Rev1")
        db.add(hp); await db.flush()

        hp_frm = Equipment(code="HP50-FRM", name_ar="هيكل المكبس",           name_en="Press Frame",        parent_id=hp.id, level=1, weight_kg=Decimal("1800"))
        hp_hyd = Equipment(code="HP50-HYD", name_ar="وحدة الهيدروليك",       name_en="Hydraulic Unit",     parent_id=hp.id, level=1, weight_kg=Decimal("650"))
        hp_ctl = Equipment(code="HP50-CTL", name_ar="لوحة التحكم",            name_en="Control Panel",      parent_id=hp.id, level=1, weight_kg=Decimal("120"))
        db.add_all([hp_frm, hp_hyd, hp_ctl]); await db.flush()

        # ══════════════════════════════════════════════════════
        # 6. EQUIPMENT DIMENSIONS
        # ══════════════════════════════════════════════════════
        dims = [
            EquipmentDimension(equipment_id=fg.id,  dim_key="length",    dim_value="3200",  unit="mm"),
            EquipmentDimension(equipment_id=fg.id,  dim_key="width",     dim_value="1800",  unit="mm"),
            EquipmentDimension(equipment_id=fg.id,  dim_key="height",    dim_value="2400",  unit="mm"),
            EquipmentDimension(equipment_id=fg.id,  dim_key="capacity",  dim_value="5",     unit="طن/ساعة"),
            EquipmentDimension(equipment_id=fg.id,  dim_key="power",     dim_value="22.5",  unit="kW"),
            EquipmentDimension(equipment_id=grd.id, dim_key="rotor_dia", dim_value="800",   unit="mm"),
            EquipmentDimension(equipment_id=grd.id, dim_key="speed",     dim_value="2950",  unit="rpm"),
            EquipmentDimension(equipment_id=scr_eq.id, dim_key="length", dim_value="1200",  unit="mm"),
            EquipmentDimension(equipment_id=scr_eq.id, dim_key="width",  dim_value="600",   unit="mm"),
            EquipmentDimension(equipment_id=sf.id,  dim_key="diameter",  dim_value="200",   unit="mm"),
            EquipmentDimension(equipment_id=sf.id,  dim_key="length",    dim_value="1500",  unit="mm"),
            EquipmentDimension(equipment_id=mx.id,  dim_key="capacity",  dim_value="2",     unit="طن/دفعة"),
            EquipmentDimension(equipment_id=mx.id,  dim_key="drum_dia",  dim_value="1200",  unit="mm"),
            EquipmentDimension(equipment_id=hp.id,  dim_key="force",     dim_value="50",    unit="طن"),
            EquipmentDimension(equipment_id=hp.id,  dim_key="stroke",    dim_value="400",   unit="mm"),
        ]
        db.add_all(dims)

        # ══════════════════════════════════════════════════════
        # 7. BOM LINES  (with plate dimensions where applicable)
        # ══════════════════════════════════════════════════════
        bom = [
            # FG-500 GRD (وحدة الطحن)
            BOMLine(equipment_id=grd.id, material_id=st52.id,  qty=Decimal("420.000"), unit_cost=Decimal("18.50"), total_cost=Decimal("7770"),
                    dim_count=4, dim_length_cm=Decimal("100"), dim_width_cm=Decimal("105"),
                    dim_area_m2=Decimal("4.20"), calc_weight_kg=Decimal("329.70"), notes="لوح 10mm لجسم المطرقة"),
            BOMLine(equipment_id=grd.id, material_id=mot15.id, qty=Decimal("1"),    unit_cost=Decimal("7200"),  total_cost=Decimal("7200"),  notes="موتور رئيسي 15KW"),
            BOMLine(equipment_id=grd.id, material_id=gbox20.id,qty=Decimal("1"),    unit_cost=Decimal("3800"),  total_cost=Decimal("3800"),  notes="علبة تروس 1:20"),
            BOMLine(equipment_id=grd.id, material_id=belt.id,  qty=Decimal("4"),    unit_cost=Decimal("45"),    total_cost=Decimal("180"),   notes="سيور نقل حركة B80"),
            BOMLine(equipment_id=grd.id, material_id=weld_rod.id,qty=Decimal("12"),  unit_cost=Decimal("35"),   total_cost=Decimal("420"),   notes="سلك لحام"),
            # FG-500 SCR (وحدة الغربلة)
            BOMLine(equipment_id=scr_eq.id, material_id=st37.id, qty=Decimal("180"), unit_cost=Decimal("16"), total_cost=Decimal("2880"),
                    dim_count=6, dim_length_cm=Decimal("120"), dim_width_cm=Decimal("60"),
                    dim_area_m2=Decimal("4.32"), calc_weight_kg=Decimal("203.47"), notes="لوح 6mm هيكل الشاشة"),
            BOMLine(equipment_id=scr_eq.id, material_id=scr3.id, qty=Decimal("4"),   unit_cost=Decimal("1200"), total_cost=Decimal("4800"),  notes="شاشات 3mm"),
            BOMLine(equipment_id=scr_eq.id, material_id=scr5.id, qty=Decimal("2"),   unit_cost=Decimal("1200"), total_cost=Decimal("2400"),  notes="شاشات 5mm"),
            BOMLine(equipment_id=scr_eq.id, material_id=mot75.id,qty=Decimal("1"),   unit_cost=Decimal("4100"), total_cost=Decimal("4100"),  notes="موتور اهتزاز 7.5KW"),
            BOMLine(equipment_id=scr_eq.id, material_id=bearing.id,qty=Decimal("4"),  unit_cost=Decimal("85"),  total_cost=Decimal("340"),   notes="بلي محاور"),
            # FG-500 FED (وحدة التغذية)
            BOMLine(equipment_id=fed.id, material_id=pipe60.id, qty=Decimal("8"),    unit_cost=Decimal("85"),   total_cost=Decimal("680"),   notes="أنابيب ملولب"),
            BOMLine(equipment_id=fed.id, material_id=hcyl.id,  qty=Decimal("2"),    unit_cost=Decimal("2800"), total_cost=Decimal("5600"),  notes="أسطوانات هيدروليك"),
            BOMLine(equipment_id=fed.id, material_id=mot3.id,  qty=Decimal("1"),    unit_cost=Decimal("2200"), total_cost=Decimal("2200"),  notes="موتور تغذية 3KW"),
            # FG-500 DRV (الإدارة)
            BOMLine(equipment_id=drv.id, material_id=inv.id,   qty=Decimal("1"),    unit_cost=Decimal("5500"), total_cost=Decimal("5500"),  notes="انفرتر 15KW"),
            BOMLine(equipment_id=drv.id, material_id=hpump.id, qty=Decimal("1"),    unit_cost=Decimal("6500"), total_cost=Decimal("6500"),  notes="طلمبة هيدروليك"),
            BOMLine(equipment_id=drv.id, material_id=plc.id,   qty=Decimal("1"),    unit_cost=Decimal("8500"), total_cost=Decimal("8500"),  notes="وحدة PLC تحكم"),
            # FG-500 FRM (الهيكل)
            BOMLine(equipment_id=frm.id, material_id=st37.id,  qty=Decimal("220"),  unit_cost=Decimal("16"),   total_cost=Decimal("3520"),
                    dim_count=8, dim_length_cm=Decimal("80"), dim_width_cm=Decimal("58"),
                    dim_area_m2=Decimal("3.71"), calc_weight_kg=Decimal("174.65"), notes="هيكل رئيسي 6mm"),
            BOMLine(equipment_id=frm.id, material_id=bolt16.id,qty=Decimal("120"),  unit_cost=Decimal("8"),    total_cost=Decimal("960"),   notes="مسامير تثبيت M16"),
            BOMLine(equipment_id=frm.id, material_id=paint.id, qty=Decimal("8"),    unit_cost=Decimal("45"),   total_cost=Decimal("360"),   notes="دهان أنتي كوروجن"),
            # MX-200 BOM
            BOMLine(equipment_id=mx_drm.id,material_id=st52.id, qty=Decimal("380"), unit_cost=Decimal("18.50"),total_cost=Decimal("7030"),
                    dim_count=3, dim_length_cm=Decimal("150"), dim_width_cm=Decimal("107"),
                    dim_area_m2=Decimal("4.82"), calc_weight_kg=Decimal("378.30"), notes="لوح 10mm اسطوانة الخلط"),
            BOMLine(equipment_id=mx_drm.id,material_id=pipe60.id,qty=Decimal("6"),  unit_cost=Decimal("85"),   total_cost=Decimal("510"),   notes="أنابيب الملولب الداخلي"),
            BOMLine(equipment_id=mx_drv.id,material_id=mot75.id, qty=Decimal("1"),  unit_cost=Decimal("4100"), total_cost=Decimal("4100"),  notes="موتور خلاط 7.5KW"),
            BOMLine(equipment_id=mx_drv.id,material_id=gbox40.id,qty=Decimal("1"),  unit_cost=Decimal("4500"), total_cost=Decimal("4500"),  notes="علبة تروس 1:40"),
            BOMLine(equipment_id=mx_frm.id,material_id=st37.id,  qty=Decimal("260"),unit_cost=Decimal("16"),   total_cost=Decimal("4160"),  notes="هيكل خلاط"),
            BOMLine(equipment_id=mx_frm.id,material_id=weld_rod.id,qty=Decimal("10"),unit_cost=Decimal("35"), total_cost=Decimal("350"),   notes="سلك لحام"),
            # HP-50 BOM
            BOMLine(equipment_id=hp_frm.id,material_id=st52.id,  qty=Decimal("800"),unit_cost=Decimal("18.50"),total_cost=Decimal("14800"), notes="هيكل مكبس 10mm"),
            BOMLine(equipment_id=hp_hyd.id,material_id=hpump.id, qty=Decimal("1"),  unit_cost=Decimal("6500"), total_cost=Decimal("6500"),  notes="طلمبة هيدروليك"),
            BOMLine(equipment_id=hp_hyd.id,material_id=hcyl.id,  qty=Decimal("4"),  unit_cost=Decimal("2800"), total_cost=Decimal("11200"), notes="أسطوانات ضغط"),
            BOMLine(equipment_id=hp_ctl.id,material_id=plc.id,   qty=Decimal("1"),  unit_cost=Decimal("8500"), total_cost=Decimal("8500"),  notes="لوحة تحكم PLC"),
        ]
        db.add_all(bom)

        # ══════════════════════════════════════════════════════
        # 8. WORKERS
        # ══════════════════════════════════════════════════════
        workers = [
            Worker(code="W-001", name="أحمد محمد سالم",    job_title="رئيس ورشة",       phone="01001234567", national_id="28501011234567", daily_wage=Decimal("350"), base_weekly_wage=Decimal("2100"), hire_date=date(2020,3,1)),
            Worker(code="W-002", name="محمود علي حسن",     job_title="لحام درجة أولى",  phone="01112345678", national_id="29002021234567", daily_wage=Decimal("250"), base_weekly_wage=Decimal("1500"), hire_date=date(2021,6,15)),
            Worker(code="W-003", name="عمر خالد إبراهيم",  job_title="خراط",            phone="01223456789", national_id="29503031234567", daily_wage=Decimal("220"), base_weekly_wage=Decimal("1320"), hire_date=date(2022,1,10)),
            Worker(code="W-004", name="كريم سامي محمد",    job_title="كهربائي",         phone="01534567890", national_id="30004041234567", daily_wage=Decimal("200"), base_weekly_wage=Decimal("1200"), hire_date=date(2022,9,1)),
            Worker(code="W-005", name="طارق عبدالله رضا",  job_title="مساعد لحام",      phone="01645678901", national_id="30105051234567", daily_wage=Decimal("150"), base_weekly_wage=Decimal("900"),  hire_date=date(2023,3,20)),
            Worker(code="W-006", name="يوسف مصطفى صالح",  job_title="عامل إنتاج",      phone="01756789012", national_id="30206061234567", daily_wage=Decimal("130"), base_weekly_wage=Decimal("780"),  hire_date=date(2023,7,1)),
            Worker(code="W-007", name="إسلام فتحي عبده",   job_title="دهان وتشطيب",    phone="01867890123", national_id="30307071234567", daily_wage=Decimal("140"), base_weekly_wage=Decimal("840"),  hire_date=date(2023,10,1)),
            Worker(code="W-008", name="رامي عادل غانم",    job_title="ميكانيكي",        phone="01978901234", national_id="30408081234567", daily_wage=Decimal("180"), base_weekly_wage=Decimal("1080"), hire_date=date(2024,1,15)),
        ]
        db.add_all(workers)
        await db.flush()

        # حضور آخر أسبوع
        today_d = date(2024, 3, 10)
        att_records = []
        for worker in workers:
            for day_offset in range(6):  # 6 أيام عمل
                d = today_d - timedelta(days=day_offset)
                status = "absent" if (day_offset == 5 and worker.code in ["W-005","W-006"]) else "present"
                att_records.append(Attendance(
                    worker_id=worker.id, date=d, status=status,
                    notes="غياب بدون إذن" if status == "absent" else None
                ))
        db.add_all(att_records)

        # كشف رواتب
        payroll = PayrollRun(week_start=date(2024,3,4), week_end=date(2024,3,10), status="draft", total_gross=Decimal("0"), total_net=Decimal("0"), total_deductions=Decimal("0"))
        db.add(payroll); await db.flush()

        payroll_lines = []
        total_payroll = Decimal("0")
        for worker in workers:
            days = 5 if worker.code in ["W-005","W-006"] else 6
            gross = worker.daily_wage * days
            payroll_lines.append(PayrollLine(
                payroll_run_id=payroll.id, worker_id=worker.id,
                days_worked=Decimal(str(days)), gross_amount=gross,
                deductions=Decimal("0"), bonus=Decimal("0"), net_amount=gross
            ))
            total_payroll += gross
        db.add_all(payroll_lines)
        payroll.total_gross = total_payroll
        payroll.total_net = total_payroll

        # ══════════════════════════════════════════════════════
        # 9. CUSTOMERS
        # ══════════════════════════════════════════════════════
        customers = [
            Customer(code="C-001", name="شركة الدلتا للدواجن",     phone="0223456789",    address="ش الجمهورية، المنصورة، الدقهلية", credit_limit=Decimal("500000"), balance=Decimal("250000")),
            Customer(code="C-002", name="مزرعة النيل للأعلاف",     phone="0234567890",      address="طريق الفيوم، الجيزة",             credit_limit=Decimal("300000"), balance=Decimal("130000")),
            Customer(code="C-003", name="مصنع الغذاء الحيواني",    phone="0245678901",    address="المنطقة الصناعية، بني سويف",      credit_limit=Decimal("750000"), balance=Decimal("0")),
            Customer(code="C-004", name="شركة الأندلس للزراعة",    phone="0256789012",  address="طريق الإسكندرية الصحراوي",        credit_limit=Decimal("200000"), balance=Decimal("9000")),
        ]
        db.add_all(customers)
        await db.flush()

        # ══════════════════════════════════════════════════════
        # 10. CUSTOMER ORDERS
        # ══════════════════════════════════════════════════════
        orders = [
            CustomerOrder(code="ORD-2024-001", customer_id=customers[0].id, equipment_id=fg.id,
                description="مجرشة FG-500 مع تركيب وتشغيل", quantity=1,
                unit_price=Decimal("250000"), total_price=Decimal("250000"),
                status="in_progress", order_date=date(2024,1,20), delivery_date=date(2024,4,30)),
            CustomerOrder(code="ORD-2024-002", customer_id=customers[1].id, equipment_id=mx.id,
                description="خلاط أعلاف MX-200 سعة 2 طن", quantity=1,
                unit_price=Decimal("130000"), total_price=Decimal("130000"),
                status="pending", order_date=date(2024,2,10), delivery_date=date(2024,5,15)),
            CustomerOrder(code="ORD-2024-003", customer_id=customers[3].id,
                description="قطع غيار شاشات غربلة 3mm", quantity=6,
                unit_price=Decimal("1500"), total_price=Decimal("9000"),
                status="delivered", order_date=date(2024,1,5), delivery_date=date(2024,1,20)),
            CustomerOrder(code="ORD-2024-004", customer_id=customers[2].id, equipment_id=hp.id,
                description="مكبس هيدروليكي 50 طن", quantity=1,
                unit_price=Decimal("320000"), total_price=Decimal("320000"),
                status="pending", order_date=date(2024,3,1), delivery_date=date(2024,7,1)),
        ]
        db.add_all(orders)

        # ══════════════════════════════════════════════════════
        # 11. QUOTATIONS
        # ══════════════════════════════════════════════════════
        qt1 = Quotation(code="QT-2024-001", customer_id=customers[0].id,
            date=date(2024,1,10), valid_until=date(2024,2,10),
            status="accepted", tax_pct=Decimal("14"), discount_pct=Decimal("0"),
            subtotal=Decimal("219298"), tax_amount=Decimal("30702"), discount_amt=Decimal("0"),
            total=Decimal("250000"), terms="50% مقدم — 50% عند التسليم",
            notes="يشمل التركيب والتشغيل والتدريب")
        qt2 = Quotation(code="QT-2024-002", customer_id=customers[1].id,
            date=date(2024,1,25), valid_until=date(2024,2,25),
            status="accepted", tax_pct=Decimal("14"), discount_pct=Decimal("5"),
            subtotal=Decimal("120000"), tax_amount=Decimal("16800"), discount_amt=Decimal("6000"),
            total=Decimal("130800"), terms="الدفع خلال 30 يوم من الاستلام",
            notes="خصم 5% للعميل الدائم")
        qt3 = Quotation(code="QT-2024-003", customer_id=customers[2].id,
            date=date(2024,2,20), valid_until=date(2024,3,20),
            status="sent", tax_pct=Decimal("14"), discount_pct=Decimal("0"),
            subtotal=Decimal("280702"), tax_amount=Decimal("39298"), discount_amt=Decimal("0"),
            total=Decimal("320000"), terms="30% مقدم — 70% عند التسليم",
            notes="مكبس هيدروليكي 50 طن كامل التجهيز")
        db.add_all([qt1, qt2, qt3]); await db.flush()

        qt_lines = [
            QuotationLine(quotation_id=qt1.id, description="مجرشة أعلاف FG-500 كاملة", qty=Decimal("1"), unit="pcs", unit_price=Decimal("200000"), total_price=Decimal("200000"), sort_order=1),
            QuotationLine(quotation_id=qt1.id, description="تركيب وتشغيل وتدريب", qty=Decimal("1"), unit="pcs", unit_price=Decimal("19298"), total_price=Decimal("19298"), sort_order=2),
            QuotationLine(quotation_id=qt2.id, description="خلاط أعلاف MX-200", qty=Decimal("1"), unit="pcs", unit_price=Decimal("115000"), total_price=Decimal("115000"), sort_order=1),
            QuotationLine(quotation_id=qt2.id, description="شحن وتركيب", qty=Decimal("1"), unit="pcs", unit_price=Decimal("5000"), total_price=Decimal("5000"), sort_order=2),
            QuotationLine(quotation_id=qt3.id, description="مكبس هيدروليكي HP-50", qty=Decimal("1"), unit="pcs", unit_price=Decimal("280702"), total_price=Decimal("280702"), sort_order=1),
        ]
        db.add_all(qt_lines)

        # ══════════════════════════════════════════════════════
        # 12. INVOICES
        # ══════════════════════════════════════════════════════
        inv1 = Invoice(code="INV-2024-001", customer_id=customers[0].id,
            date=date(2024,1,22), due_date=date(2024,2,22),
            status="partial", tax_pct=Decimal("14"), discount_pct=Decimal("0"),
            subtotal=Decimal("109649"), tax_amount=Decimal("15351"), discount_amt=Decimal("0"),
            total=Decimal("125000"), paid_amount=Decimal("125000"),
            terms="50% مقدم", notes="دفعة أولى مجرشة FG-500")
        inv2 = Invoice(code="INV-2024-002", customer_id=customers[1].id,
            date=date(2024,2,12), due_date=date(2024,3,12),
            status="unpaid", tax_pct=Decimal("14"), discount_pct=Decimal("5"),
            subtotal=Decimal("120000"), tax_amount=Decimal("16800"), discount_amt=Decimal("6000"),
            total=Decimal("130800"), paid_amount=Decimal("0"),
            terms="30 يوم من تاريخ الفاتورة", notes="خلاط MX-200")
        inv3 = Invoice(code="INV-2024-003", customer_id=customers[3].id,
            date=date(2024,1,20), due_date=date(2024,2,20),
            status="unpaid", tax_pct=Decimal("0"), discount_pct=Decimal("0"),
            subtotal=Decimal("9000"), tax_amount=Decimal("0"), discount_amt=Decimal("0"),
            total=Decimal("9000"), paid_amount=Decimal("0"),
            notes="قطع غيار شاشات")
        db.add_all([inv1, inv2, inv3]); await db.flush()

        inv_lines = [
            InvoiceLine(invoice_id=inv1.id, description="دفعة أولى — مجرشة FG-500", qty=Decimal("1"), unit="pcs", unit_price=Decimal("109649"), total_price=Decimal("109649"), sort_order=1),
            InvoiceLine(invoice_id=inv2.id, description="خلاط أعلاف MX-200 كامل", qty=Decimal("1"), unit="pcs", unit_price=Decimal("120000"), total_price=Decimal("120000"), sort_order=1),
            InvoiceLine(invoice_id=inv3.id, description="شاشات غربلة 3mm", qty=Decimal("4"), unit="pcs", unit_price=Decimal("1200"), total_price=Decimal("4800"), sort_order=1),
            InvoiceLine(invoice_id=inv3.id, description="شاشات غربلة 5mm", qty=Decimal("2"), unit="pcs", unit_price=Decimal("1200"), total_price=Decimal("2400"), sort_order=2),
            InvoiceLine(invoice_id=inv3.id, description="رسوم شحن", qty=Decimal("1"), unit="pcs", unit_price=Decimal("1800"), total_price=Decimal("1800"), sort_order=3),
        ]
        db.add_all(inv_lines)

        pay1 = CustomerPayment(customer_id=customers[0].id,
            amount=Decimal("125000"), payment_date=date(2024,1,23), notes="تحويل بنكي — دفعة أولى")
        db.add(pay1)

        # ══════════════════════════════════════════════════════
        # 13. WORK ORDERS + COST LINES
        # ══════════════════════════════════════════════════════
        wo1 = WorkOrder(code="WO-2024-001", equipment_id=fg.id, status="in_progress",
            planned_cost=Decimal("185000"), actual_cost=Decimal("0"),
            start_date=date(2024,2,1), end_date=date(2024,4,15),
            notes="FG-500 لشركة الدلتا للدواجن — ORD-2024-001")
        wo2 = WorkOrder(code="WO-2024-002", equipment_id=mx.id, status="draft",
            planned_cost=Decimal("95000"), actual_cost=Decimal("0"),
            start_date=date(2024,3,1), end_date=date(2024,4,30),
            notes="MX-200 لمزرعة النيل — ORD-2024-002")
        wo3 = WorkOrder(code="WO-2024-003", equipment_id=hp.id, status="draft",
            planned_cost=Decimal("220000"), actual_cost=Decimal("0"),
            start_date=date(2024,4,1), end_date=date(2024,6,30),
            notes="HP-50 لمصنع الغذاء الحيواني — ORD-2024-004")
        wo4 = WorkOrder(code="WO-2023-089", equipment_id=fg.id, status="done",
            planned_cost=Decimal("175000"), actual_cost=Decimal("168500"),
            start_date=date(2023,10,1), end_date=date(2023,12,20),
            notes="FG-500 مكتمل — تم التسليم")
        db.add_all([wo1, wo2, wo3, wo4]); await db.flush()

        cost_lines = [
            # WO-2024-001 (جاري)
            CostLine(work_order_id=wo1.id, cost_type="material", description="فولاذ ST-52 هيكل وطحن",  qty=Decimal("600"),  unit_cost=Decimal("18.50"), total_cost=Decimal("11100"), material_id=st52.id),
            CostLine(work_order_id=wo1.id, cost_type="material", description="موتور 15KW",              qty=Decimal("1"),    unit_cost=Decimal("7200"),  total_cost=Decimal("7200"),  material_id=mot15.id),
            CostLine(work_order_id=wo1.id, cost_type="material", description="انفرتر تحكم 15KW",        qty=Decimal("1"),    unit_cost=Decimal("5500"),  total_cost=Decimal("5500"),  material_id=inv.id),
            CostLine(work_order_id=wo1.id, cost_type="material", description="علبة تروس وسيور",         qty=Decimal("1"),    unit_cost=Decimal("4000"),  total_cost=Decimal("4000")),
            CostLine(work_order_id=wo1.id, cost_type="labor",    description="لحام وتصنيع هيكل",       qty=Decimal("80"),   unit_cost=Decimal("250"),   total_cost=Decimal("20000")),
            CostLine(work_order_id=wo1.id, cost_type="labor",    description="تجميع وضبط وتشغيل",      qty=Decimal("40"),   unit_cost=Decimal("200"),   total_cost=Decimal("8000")),
            CostLine(work_order_id=wo1.id, cost_type="overhead", description="كهرباء وغاز وغيره",      qty=Decimal("1"),    unit_cost=Decimal("6500"),  total_cost=Decimal("6500")),
            # WO-2023-089 (منتهي)
            CostLine(work_order_id=wo4.id, cost_type="material", description="مواد خام",               qty=Decimal("1"),    unit_cost=Decimal("98000"), total_cost=Decimal("98000")),
            CostLine(work_order_id=wo4.id, cost_type="labor",    description="عمالة إجمالية",          qty=Decimal("1"),    unit_cost=Decimal("52000"), total_cost=Decimal("52000")),
            CostLine(work_order_id=wo4.id, cost_type="overhead", description="مصاريف غير مباشرة",     qty=Decimal("1"),    unit_cost=Decimal("18500"), total_cost=Decimal("18500")),
        ]
        db.add_all(cost_lines)
        wo1.actual_cost = sum(c.total_cost for c in cost_lines if c.work_order_id == wo1.id)

        await db.commit()

        # ── Summary ───────────────────────────────────────────
        print("\n✅  Seed complete — ورشة معدات الأعلاف\n")
        print("=" * 55)
        print(f"  👥  المستخدمون   : {len(users)} (admin / accountant / production / viewer)")
        print(f"  📦  المواد       : {len(mats)} صنف في {len(cats)} تصنيف")
        print(f"  🔩  ألواح فولاذ  : 4 مواد بكثافة محسوبة")
        print(f"  📊  حركات مخزون  : {len(movements)}")
        print(f"  ⚙️   معدات        : 3 مجموعات رئيسية (FG-500 / MX-200 / HP-50)")
        print(f"  📐  BOM سطور     : {len(bom)} (تشمل أبعاد الألواح)")
        print(f"  👷  عاملون       : {len(workers)}")
        print(f"  🏢  عملاء        : {len(customers)}")
        print(f"  📋  طلبات        : {len(orders)}")
        print(f"  📄  عروض أسعار  : 3 (1 مرسل، 2 مقبول)")
        print(f"  🧾  فواتير       : 3 (1 جزئي، 2 غير مدفوع)")
        print(f"  🏭  أوامر إنتاج  : 4 (2 مسودة / 1 جاري / 1 منتهي)")
        print("=" * 55)
        print("  بيانات الدخول:")
        print("    admin        / admin123")
        print("    accountant   / accountant123")
        print("    production   / production123")
        print("    viewer       / viewer123")
        print("=" * 55)


if __name__ == "__main__":
    async def _main():
        await wipe_all()
        await seed()
    asyncio.run(_main())
