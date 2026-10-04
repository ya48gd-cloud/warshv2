"""
nesting.py — مولّد مخطط التقطيع (Cutting Plan / 2D Nesting)
خوارزمية رص على أرفف (Shelf / Guillotine, NFDH) مع دعم التدوير 90°.

تستخدم لترتيب مجموعة من القطع المطلوبة (مقاسات + كميات) على أقل
عدد ممكن من الألواح بمقاس معين. الأولوية: تجميع الهالك في شريط واحد
أسفل اللوح (وحواف يمين كل رف عند الحاجة) بقدر الإمكان، بحيث يكون
القص عمليًا بخطوط مستقيمة (guillotine cuts) بدل قطع مبعثرة.
"""
from dataclasses import dataclass, field


TOL = 1e-6

# حد أقصى لإجمالي عدد القطع في مخطط واحد — حماية من استهلاك ذاكرة/معالج غير
# محدود (كل قطعة بتتفرد كعنصر مستقل في الخوارزمية)
MAX_TOTAL_PIECES = 200000


def _validate_inputs(pieces: list[dict], kerf: float) -> None:
    """فحوصات مشتركة لكل مولّدات مخططات التقطيع (دفاع إضافي فوق فحوصات الـAPI)."""
    if kerf is None or kerf < 0:
        raise ValueError("سُمك القطع (kerf) لا يمكن أن يكون سالباً")
    total = 0
    for p in pieces or []:
        qty = int(p.get("qty", 1))
        if qty < 0:
            raise ValueError(f"عدد القطع '{p.get('label', '')}' لا يمكن أن يكون سالباً")
        total += qty
    if total > MAX_TOTAL_PIECES:
        raise ValueError(f"إجمالي عدد القطع ({total}) يتجاوز الحد الأقصى ({MAX_TOTAL_PIECES})")


def _merge_free_rects(rects: list[dict]) -> list[dict]:
    """يدمج مستطيلات الهالك المتجاورة (نفس العرض/الارتفاع ومتلاصقة) في مستطيل واحد أكبر،
    لتقليل عدد "قطع الهالك" المعروضة بقدر الإمكان."""
    rects = [dict(r) for r in rects if r["width"] > TOL and r["height"] > TOL]
    merged = True
    while merged:
        merged = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                if abs(a["x"] - b["x"]) < TOL and abs(a["width"] - b["width"]) < TOL:
                    if abs(a["y"] + a["height"] - b["y"]) < TOL:
                        a["height"] += b["height"]
                        rects.pop(j)
                        merged = True
                        break
                    if abs(b["y"] + b["height"] - a["y"]) < TOL:
                        a["y"] = b["y"]
                        a["height"] += b["height"]
                        rects.pop(j)
                        merged = True
                        break
                if abs(a["y"] - b["y"]) < TOL and abs(a["height"] - b["height"]) < TOL:
                    if abs(a["x"] + a["width"] - b["x"]) < TOL:
                        a["width"] += b["width"]
                        rects.pop(j)
                        merged = True
                        break
                    if abs(b["x"] + b["width"] - a["x"]) < TOL:
                        a["x"] = b["x"]
                        a["width"] += b["width"]
                        rects.pop(j)
                        merged = True
                        break
            if merged:
                break
    return rects


@dataclass
class Placement:
    label: str
    x: float
    y: float
    width: float
    height: float
    rotated: bool


class ShelfPacker:
    """
    يرص القطع في "أرفف" أفقية بعرض اللوح، كل رف ارتفاعه = أطول قطعة فيه.
    القطعة الجديدة تُوضع على الرف الذي يترك أقل هالك ارتفاع ممكن،
    وإلا تبدأ رفًا جديدًا أسفل آخر رف مستخدم.
    """

    def __init__(self, width: float, height: float):
        self.width = width
        self.height = height
        self.shelves: list[dict] = []
        self.used_height = 0.0
        self.placements: list[Placement] = []
        self.free_pool: list[dict] | None = None

    def insert(self, w: float, h: float, allow_rotate: bool, label: str) -> bool:
        candidates = [(w, h, False)]
        if allow_rotate and abs(w - h) > TOL:
            candidates.append((h, w, True))

        best = None  # (score, idx, pw, ph, rot)
        for idx, shelf in enumerate(self.shelves):
            for (pw, ph, rot) in candidates:
                if ph <= shelf["height"] + TOL and shelf["used_width"] + pw <= self.width + TOL:
                    waste = shelf["height"] - ph
                    score = (waste, idx)
                    if best is None or score < best[0]:
                        best = (score, idx, pw, ph, rot)

        if best is not None:
            _, idx, pw, ph, rot = best
            shelf = self.shelves[idx]
            x = shelf["used_width"]
            y = shelf["y"] + (shelf["height"] - ph)
            self.placements.append(Placement(label=label, x=x, y=y, width=pw, height=ph, rotated=rot))
            shelf["items"].append({"x": x, "width": pw, "height": ph})
            shelf["used_width"] += pw
            return True

        for (pw, ph, rot) in candidates:
            if pw <= self.width + TOL and self.used_height + ph <= self.height + TOL:
                self.placements.append(Placement(label=label, x=0.0, y=self.used_height, width=pw, height=ph, rotated=rot))
                self.shelves.append({
                    "y": self.used_height, "height": ph, "used_width": pw,
                    "items": [{"x": 0.0, "width": pw, "height": ph}],
                })
                self.used_height += ph
                return True

        return False

    def expand_last_shelf(self) -> None:
        """
        يوسّع ارتفاع آخر رف ليغطي الارتفاع المتبقي من اللوح بالكامل،
        بحيث تتاح مساحة فاضية أكبر يمين آخر رف يمكن حشر قطع أخرى (من نفس
        اللوح أو من ألواح لاحقة) فيها (تكديس عمودي) بدل تركها هالك مبعثر.
        """
        if not self.shelves:
            return
        last = self.shelves[-1]
        new_height = self.height - last["y"]
        if new_height > last["height"] + TOL:
            last["height"] = new_height
            self.used_height = self.height

    def get_free_pool(self) -> list[dict]:
        """يهيّئ مجمّع مناطق الهالك (مرة واحدة) من waste_rects() الأساسية."""
        if self.free_pool is None:
            self.expand_last_shelf()
            self.free_pool = _merge_free_rects(self._shelf_waste_rects())
        return self.free_pool

    def consume(self, pw: float, ph: float, rot: bool, label: str) -> bool:
        """
        يحاول وضع قطعة (pw×ph) داخل أفضل منطقة هالك متاحة (أقل فراغ متبقي)،
        ثم يقسّم المنطقة المتبقية (guillotine split) ويحدّث المجمّع.
        """
        pool = self.get_free_pool()
        best = None  # (leftover_area, idx)
        for idx, r in enumerate(pool):
            if pw <= r["width"] + TOL and ph <= r["height"] + TOL:
                leftover = r["width"] * r["height"] - pw * ph
                if best is None or leftover < best[0]:
                    best = (leftover, idx)
        if best is None:
            return False

        idx = best[1]
        r = pool.pop(idx)
        fx, fy, fw, fh = r["x"], r["y"], r["width"], r["height"]
        self.placements.append(Placement(label=label, x=fx, y=fy, width=pw, height=ph, rotated=rot))

        right_w = fw - pw
        bottom_h = fh - ph
        if right_w > TOL and bottom_h > TOL:
            opt1_big = fw * bottom_h
            opt2_big = fh * right_w
            if opt1_big >= opt2_big:
                pool.append({"x": round(fx, 3), "y": round(fy + ph, 3), "width": round(fw, 3), "height": round(bottom_h, 3)})
                pool.append({"x": round(fx + pw, 3), "y": round(fy, 3), "width": round(right_w, 3), "height": round(ph, 3)})
            else:
                pool.append({"x": round(fx + pw, 3), "y": round(fy, 3), "width": round(right_w, 3), "height": round(fh, 3)})
                pool.append({"x": round(fx, 3), "y": round(fy + ph, 3), "width": round(pw, 3), "height": round(bottom_h, 3)})
        elif right_w > TOL:
            pool.append({"x": round(fx + pw, 3), "y": round(fy, 3), "width": round(right_w, 3), "height": round(fh, 3)})
        elif bottom_h > TOL:
            pool.append({"x": round(fx, 3), "y": round(fy + ph, 3), "width": round(fw, 3), "height": round(bottom_h, 3)})

        self.free_pool = _merge_free_rects(pool)
        return True

    def add_vacated(self, x: float, y: float, w: float, h: float) -> None:
        """يسجّل منطقة أصبحت فاضية (بعد نقل قطعة منها لشيت آخر) كهالك."""
        pool = self.get_free_pool()
        pool.append({"x": round(x, 3), "y": round(y, 3), "width": round(w, 3), "height": round(h, 3)})
        self.free_pool = _merge_free_rects(pool)

    def waste_rects(self) -> list[dict]:
        return self.get_free_pool()

    def _shelf_waste_rects(self) -> list[dict]:
        """
        مناطق الهالك: لكل رف — الفجوة فوق كل قطعة أقصر من ارتفاع الرف
        + الجزء الفاضي على يمين الرف، بالإضافة للشريط الفاضي أسفل آخر رف.
        """
        rects = []
        for shelf in self.shelves:
            for item in shelf["items"]:
                gap_h = shelf["height"] - item["height"]
                if gap_h > TOL:
                    rects.append({
                        "x": round(item["x"], 3),
                        "y": round(shelf["y"], 3),
                        "width": round(item["width"], 3),
                        "height": round(gap_h, 3),
                    })
            leftover_w = self.width - shelf["used_width"]
            if leftover_w > TOL:
                rects.append({
                    "x": round(shelf["used_width"], 3),
                    "y": round(shelf["y"], 3),
                    "width": round(leftover_w, 3),
                    "height": round(shelf["height"], 3),
                })
        leftover_h = self.height - self.used_height
        if leftover_h > TOL:
            rects.append({
                "x": 0.0,
                "y": round(self.used_height, 3),
                "width": round(self.width, 3),
                "height": round(leftover_h, 3),
            })
        return rects


def _best_orientation(w: float, h: float, qty: int, sheet_w: float, sheet_h: float, allow_rotation: bool):
    """
    يحدد اتجاه القطعة (عرض×ارتفاع) الذي يحقق أقل عدد خلايا فاضية في شبكة منتظمة
    على عرض اللوح، وأقل ارتفاع إجمالي مستخدم — بحيث تتجمع القطع المتماثلة في
    صفوف كاملة ويتجمع الهالك في شريط واحد أسفل اللوح بقدر الإمكان.
    """
    options = [(w, h)]
    if allow_rotation and abs(w - h) > TOL:
        options.append((h, w))

    best = None
    for (pw, ph) in options:
        if pw > sheet_w + TOL or ph > sheet_h + TOL:
            continue
        cols = max(int((sheet_w + TOL) // pw), 1)
        rows = -(-qty // cols)  # ceil division
        empty_cells = rows * cols - qty
        total_height = rows * ph
        score = (empty_cells, total_height)
        if best is None or score < best[0]:
            best = (score, pw, ph)

    if best is None:
        return w, h
    return best[1], best[2]


def _transpose_packer(packer: ShelfPacker, sheet_w: float, sheet_h: float) -> ShelfPacker:
    """
    يحوّل نتيجة تعبئة تمت على لوح بأبعاد معكوسة (sheet_h × sheet_w) إلى نظام
    إحداثيات اللوح الأصلي (sheet_w × sheet_h) بتبديل المحورين x↔y والعرض↔الارتفاع.
    يُستخدم لتجربة تعبئة اللوح بالاتجاه الآخر (مفيد للقطع الطويلة الرفيعة جدًا).
    """
    transposed = ShelfPacker(sheet_w, sheet_h)
    transposed.placements = [
        Placement(label=pl.label, x=pl.y, y=pl.x, width=pl.height, height=pl.width, rotated=not pl.rotated)
        for pl in packer.placements
    ]
    transposed.free_pool = [
        {"x": r["y"], "y": r["x"], "width": r["height"], "height": r["width"]}
        for r in packer.get_free_pool()
    ]
    return transposed


def _compact_into_earlier_sheets(packers: list[ShelfPacker], allow_rotation: bool) -> list[ShelfPacker]:
    """
    يحاول نقل قطع الألواح الأخيرة إلى مناطق الهالك في الألواح السابقة،
    بحيث يتجمّع الهالك في أقل عدد من الألواح (يصبح اللوح الأخير كله هالك تقريبًا).
    """
    for i in range(len(packers) - 1, 0, -1):
        src = packers[i]
        kept = []
        for pl in src.placements:
            candidates = [(pl.width, pl.height, pl.rotated)]
            if allow_rotation and abs(pl.width - pl.height) > TOL:
                candidates.append((pl.height, pl.width, not pl.rotated))

            moved = False
            for j in range(i):
                tgt = packers[j]
                for (pw, ph, rot) in candidates:
                    if tgt.consume(pw, ph, rot, pl.label):
                        moved = True
                        break
                if moved:
                    break

            if moved:
                src.add_vacated(pl.x, pl.y, pl.width, pl.height)
            else:
                kept.append(pl)
        src.placements = kept

    return [p for p in packers if p.placements]


def _pack_grouped(items_grouped: list[dict], sheet_w: float, sheet_h: float) -> list[ShelfPacker]:
    """تعبئة بترتيب-اتجاه موحّد لكل مجموعة (يحافظ على شكل موحّد للهالك)."""
    packers: list[ShelfPacker] = []
    for item in items_grouped:
        placed = False
        for packer in packers:
            if packer.insert(item["w"], item["h"], False, item["label"]):
                packer.placements[-1].rotated = item["rotated"]
                placed = True
                break
        if not placed:
            packer = ShelfPacker(sheet_w, sheet_h)
            if not packer.insert(item["w"], item["h"], False, item["label"]):
                raise ValueError(f"تعذّر وضع القطعة '{item['label']}' على لوح جديد")
            packer.placements[-1].rotated = item["rotated"]
            packers.append(packer)
    return packers


def _pack_free(items_free: list[dict], sheet_w: float, sheet_h: float, allow_rotation: bool) -> list[ShelfPacker]:
    """تعبئة بحرية تدوير كاملة لكل قطعة على حدة (تبحث عن أفضل ترتيب إجمالي)."""
    packers: list[ShelfPacker] = []
    for item in items_free:
        placed = False
        for packer in packers:
            if packer.insert(item["w"], item["h"], allow_rotation, item["label"]):
                placed = True
                break
        if not placed:
            packer = ShelfPacker(sheet_w, sheet_h)
            if not packer.insert(item["w"], item["h"], allow_rotation, item["label"]):
                raise ValueError(f"تعذّر وضع القطعة '{item['label']}' على لوح جديد")
            packers.append(packer)
    return packers


def build_cutting_plan(sheet_w: float, sheet_h: float, pieces: list[dict],
                        allow_rotation: bool = True, kerf: float = 0.0) -> dict:
    """
    pieces: [{"label": str, "width": float, "height": float, "qty": int}]
    kerf: المسافة المُهدرة بين القطع (سُمك شفرة القص) بنفس وحدة المقاسات.
    """
    if sheet_w <= 0 or sheet_h <= 0:
        raise ValueError("مقاس اللوح غير صالح")
    _validate_inputs(pieces, kerf)

    items_grouped = []
    items_free = []
    total_items = 0
    for p in pieces:
        w = float(p["width"]) + kerf
        h = float(p["height"]) + kerf
        if float(p["width"]) <= 0 or float(p["height"]) <= 0:
            raise ValueError(f"مقاس القطعة '{p['label']}' غير صالح")
        qty = int(p.get("qty", 1))
        fits = (w <= sheet_w + TOL and h <= sheet_h + TOL) or \
               (allow_rotation and h <= sheet_w + TOL and w <= sheet_h + TOL)
        if not fits:
            raise ValueError(f"القطعة '{p['label']}' ({p['width']}×{p['height']}) أكبر من اللوح ({sheet_w}×{sheet_h})")

        pw, ph = _best_orientation(w, h, qty, sheet_w, sheet_h, allow_rotation)
        forced_rotated = abs(pw - w) > TOL
        total_items += qty
        for _ in range(qty):
            items_grouped.append({"label": p["label"], "w": pw, "h": ph, "rotated": forced_rotated})
            items_free.append({"label": p["label"], "w": w, "h": h})

    # ترتيب تنازلي بالارتفاع (ثم العرض) لتجميع القطع المتشابهة في أرفف واحدة
    items_grouped.sort(key=lambda it: (it["h"], it["w"]), reverse=True)

    # عدة استراتيجيات: نجرّبها ونختار الأقل عددًا في الألواح، ثم الأعلى كفاءة،
    # مع تفضيل الاستراتيجية الموحّدة (هالك أنظف) عند التساوي.
    strategies = [_pack_grouped(items_grouped, sheet_w, sheet_h)]

    if allow_rotation:
        by_area = sorted(items_free, key=lambda it: it["w"] * it["h"], reverse=True)
        by_maxdim = sorted(items_free, key=lambda it: (max(it["w"], it["h"]), min(it["w"], it["h"])), reverse=True)
        strategies.append(_pack_free(by_area, sheet_w, sheet_h, allow_rotation))
        strategies.append(_pack_free(by_maxdim, sheet_w, sheet_h, allow_rotation))

        if abs(sheet_w - sheet_h) > TOL:
            strategies.append([
                _transpose_packer(p, sheet_w, sheet_h)
                for p in _pack_free(by_area, sheet_h, sheet_w, allow_rotation)
            ])
            strategies.append([
                _transpose_packer(p, sheet_w, sheet_h)
                for p in _pack_free(by_maxdim, sheet_h, sheet_w, allow_rotation)
            ])

    best_packers = None
    best_score = None
    for candidate in strategies:
        candidate = _compact_into_earlier_sheets(candidate, allow_rotation)
        used_total = sum(
            sum((pl.width - kerf) * (pl.height - kerf) for pl in packer.placements)
            for packer in candidate
        )
        waste_count = sum(len(packer.waste_rects()) for packer in candidate)
        score = (len(candidate), -used_total, waste_count)
        if best_score is None or score < best_score:
            best_score = score
            best_packers = candidate

    packers = best_packers

    sheet_area = sheet_w * sheet_h
    sheets = []
    total_used = 0.0
    for idx, packer in enumerate(packers, start=1):
        used = sum((pl.width - kerf) * (pl.height - kerf) for pl in packer.placements)
        used = max(used, 0.0)
        total_used += used
        sheets.append({
            "index": idx,
            "placements": [
                {
                    "label": pl.label,
                    "x": round(pl.x, 3),
                    "y": round(pl.y, 3),
                    "width": round(pl.width - kerf, 3),
                    "height": round(pl.height - kerf, 3),
                    "rotated": pl.rotated,
                }
                for pl in packer.placements
            ],
            "waste_rects": packer.waste_rects(),
            "used_area": round(used, 3),
            "waste_area": round(sheet_area - used, 3),
            "efficiency_pct": round((used / sheet_area) * 100, 2) if sheet_area else 0,
        })

    total_sheets = len(sheets)
    overall_efficiency = round((total_used / (sheet_area * total_sheets)) * 100, 2) if total_sheets else 0

    return {
        "sheet_width": sheet_w,
        "sheet_height": sheet_h,
        "total_pieces": total_items,
        "total_sheets": total_sheets,
        "overall_efficiency_pct": overall_efficiency,
        "sheets": sheets,
    }


def build_cutting_plan_from_stock(stock_items: list[dict], pieces: list[dict],
                                   allow_rotation: bool = True, kerf: float = 0.0) -> dict:
    """
    يختار أفضل توليفة من ألواح/نسالة متاحة بمقاسات مختلفة لتغطية القطع المطلوبة.
    أولوية الاستخدام: النسالة أولاً، ثم أصغر مقاس قياسي كافٍ.

    stock_items: [{"id": int, "length": float, "width": float, "qty": int, "source": "purchased"|"remnant"}]
    pieces: [{"label": str, "width": float, "height": float, "qty": int}]
    """
    _validate_inputs(pieces, kerf)
    items = []
    for p in pieces:
        w = float(p["width"]) + kerf
        h = float(p["height"]) + kerf
        if float(p["width"]) <= 0 or float(p["height"]) <= 0:
            raise ValueError(f"مقاس القطعة '{p['label']}' غير صالح")
        for _ in range(int(p.get("qty", 1))):
            items.append({"label": p["label"], "w": w, "h": h})
    items.sort(key=lambda it: max(it["w"], it["h"]), reverse=True)

    # ترتيب المخزون: النسالة أولاً، ثم تصاعديًا بالمساحة
    pool = sorted(
        ({**s, "remaining": int(s["qty"])} for s in stock_items),
        key=lambda s: (0 if s["source"] == "remnant" else 1, float(s["length"]) * float(s["width"])),
    )

    sheets = []
    remaining_items = items[:]

    while remaining_items:
        progressed = False
        for stock in pool:
            if stock["remaining"] <= 0:
                continue
            sw, sh = float(stock["length"]), float(stock["width"])
            packer = ShelfPacker(sw, sh)
            placed, not_placed = [], []
            for item in remaining_items:
                if packer.insert(item["w"], item["h"], allow_rotation, item["label"]):
                    placed.append(item)
                else:
                    not_placed.append(item)
            if not placed:
                continue

            stock["remaining"] -= 1
            sheet_area = sw * sh
            used = sum((pl.width - kerf) * (pl.height - kerf) for pl in packer.placements)
            used = max(used, 0.0)
            sheets.append({
                "index": len(sheets) + 1,
                "stock_item_id": stock.get("id"),
                "source": stock["source"],
                "sheet_width": sw,
                "sheet_height": sh,
                "placements": [
                    {
                        "label": pl.label,
                        "x": round(pl.x, 3),
                        "y": round(pl.y, 3),
                        "width": round(pl.width - kerf, 3),
                        "height": round(pl.height - kerf, 3),
                        "rotated": pl.rotated,
                    }
                    for pl in packer.placements
                ],
                "waste_rects": packer.waste_rects(),
                "used_area": round(used, 3),
                "waste_area": round(sheet_area - used, 3),
                "efficiency_pct": round((used / sheet_area) * 100, 2) if sheet_area else 0,
            })
            remaining_items = not_placed
            progressed = True
            break  # أعد المحاولة من بداية القائمة (تفضيل النسالة دائمًا)

        if not progressed:
            break

    # تجميع النواقص حسب الكود
    shortage_counts: dict[str, int] = {}
    for item in remaining_items:
        shortage_counts[item["label"]] = shortage_counts.get(item["label"], 0) + 1
    shortage = [{"label": label, "qty": qty} for label, qty in shortage_counts.items()]

    return {
        "total_pieces": len(items),
        "total_sheets": len(sheets),
        "sheets": sheets,
        "shortage": shortage,
    }


def build_bar_cutting_plan_from_stock(stock_items: list[dict], pieces: list[dict], kerf: float = 0.0) -> dict:
    """
    يختار أفضل توليفة من أسياخ/نسالة متاحة بأطوال مختلفة لتغطية القطع المطلوبة (1D).
    أولوية الاستخدام: النسالة أولاً، ثم أصغر طول كافٍ.

    stock_items: [{"id": int, "length": float, "qty": int, "source": "purchased"|"remnant"}]
    pieces: [{"label": str, "length": float, "qty": int}]
    """
    _validate_inputs(pieces, kerf)
    items = []
    for p in pieces:
        length = float(p["length"])
        if length <= 0:
            raise ValueError(f"مقاس القطعة '{p['label']}' غير صالح")
        for _ in range(int(p.get("qty", 1))):
            items.append({"label": p["label"], "length": length})
    items.sort(key=lambda it: it["length"], reverse=True)

    # ترتيب المخزون: النسالة أولاً، ثم تصاعديًا بالطول
    pool = sorted(
        ({**s, "remaining": int(s["qty"])} for s in stock_items),
        key=lambda s: (0 if s["source"] == "remnant" else 1, float(s["length"])),
    )

    bars = []
    remaining_items = items[:]

    while remaining_items:
        progressed = False
        for stock in pool:
            if stock["remaining"] <= 0:
                continue
            bar_length = float(stock["length"])
            cuts, not_placed = [], []
            free = bar_length
            for item in remaining_items:
                need = item["length"] + kerf
                if need <= free + TOL:
                    cuts.append(item)
                    free -= need
                else:
                    not_placed.append(item)
            if not cuts:
                continue

            stock["remaining"] -= 1
            used = sum(c["length"] for c in cuts)
            waste = bar_length - used - kerf * len(cuts)
            bars.append({
                "index": len(bars) + 1,
                "stock_item_id": stock.get("id"),
                "source": stock["source"],
                "bar_length": bar_length,
                "cuts": [{"label": c["label"], "length": round(c["length"], 3)} for c in cuts],
                "used_length": round(used, 3),
                "waste_length": round(max(waste, 0.0), 3),
                "efficiency_pct": round((used / bar_length) * 100, 2) if bar_length else 0,
            })
            remaining_items = not_placed
            progressed = True
            break  # أعد المحاولة من بداية القائمة (تفضيل النسالة دائمًا)

        if not progressed:
            break

    shortage_counts: dict[str, int] = {}
    for item in remaining_items:
        shortage_counts[item["label"]] = shortage_counts.get(item["label"], 0) + 1
    shortage = [{"label": label, "qty": qty} for label, qty in shortage_counts.items()]

    return {
        "total_pieces": len(items),
        "total_bars": len(bars),
        "bars": bars,
        "shortage": shortage,
    }


def build_bar_cutting_plan(bar_length: float, pieces: list[dict], kerf: float = 0.0) -> dict:
    """
    مولّد مخطط تقطيع القضبان/الحديد (1D Cutting Stock) — خوارزمية First-Fit Decreasing.
    ترتّب القطع المطلوبة (أطوال + كميات) على أقل عدد ممكن من القضبان بطول ثابت.

    pieces: [{"label": str, "length": float, "qty": int}]
    """
    if bar_length <= 0:
        raise ValueError("طول القضيب غير صالح")
    _validate_inputs(pieces, kerf)

    items = []
    for p in pieces:
        length = float(p["length"])
        if length <= 0:
            raise ValueError(f"مقاس القطعة '{p['label']}' غير صالح")
        if length > bar_length + TOL:
            raise ValueError(f"القطعة '{p['label']}' ({length} سم) أطول من القضيب ({bar_length} سم)")
        for _ in range(int(p.get("qty", 1))):
            items.append({"label": p["label"], "length": length})
    items.sort(key=lambda it: it["length"], reverse=True)

    bars: list[dict] = []
    for item in items:
        need = item["length"] + kerf
        for bar in bars:
            if bar["remaining"] >= need - TOL:
                bar["cuts"].append(item)
                bar["remaining"] -= need
                break
        else:
            bars.append({"remaining": bar_length - need, "cuts": [item]})

    result_bars = []
    for i, bar in enumerate(bars, start=1):
        used = sum(c["length"] for c in bar["cuts"])
        waste = bar_length - used - kerf * len(bar["cuts"])
        result_bars.append({
            "index": i,
            "bar_length": bar_length,
            "cuts": [{"label": c["label"], "length": round(c["length"], 3)} for c in bar["cuts"]],
            "used_length": round(used, 3),
            "waste_length": round(max(waste, 0.0), 3),
            "efficiency_pct": round((used / bar_length) * 100, 2) if bar_length else 0,
        })

    total_used = sum(b["used_length"] for b in result_bars)
    total_length = bar_length * len(result_bars)

    return {
        "bar_length": bar_length,
        "total_pieces": len(items),
        "total_bars": len(result_bars),
        "overall_efficiency_pct": round((total_used / total_length) * 100, 2) if total_length else 0,
        "bars": result_bars,
    }
