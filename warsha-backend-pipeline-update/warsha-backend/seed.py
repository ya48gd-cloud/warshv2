"""
seed.py — بيانات تجريبية شاملة لنظام WarshERP v3.0
ورشة تصنيع معدات مصانع الأعلاف

Run: docker compose exec backend python seed.py
"""
import asyncio
import hashlib
from decimal import Decimal
from datetime import date, datetime, timedelta

from app.core.database import AsyncSessionLocal, engine
from app.models.models import Base


def _hash(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def dec(v):
    return Decimal(str(v)) if v is not None else None


async def wipe_all():
    """
    مسح آمن: TRUNCATE مع CASCADE بدل drop_all
    يتجنب مشاكل الـ constraints المختلفة بين SQLAlchemy والـ DB الفعلية
    """
    async with engine.begin() as conn:
        # جلب أسماء الجداول الفعلية من الـ DB
        result = await conn.execute(
            __import__("sqlalchemy").text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename != 'alembic_version' ORDER BY tablename"
            )
        )
        tables = [row[0] for row in result.fetchall()]
        if tables:
            tables_str = ", ".join(f'"{t}"' for t in tables)
            await conn.execute(
                __import__("sqlalchemy").text(
                    f"TRUNCATE TABLE {tables_str} RESTART IDENTITY CASCADE"
                )
            )
    print(f"🗑  تم مسح كل البيانات ({len(tables) if tables else 0} جدول) — قاعدة بيانات نظيفة")


async def seed():
    async with AsyncSessionLocal() as db:
        from app.models.models import (
            Tenant, User, MaterialCategory, Material, StockMovement,
            Equipment, EquipmentDimension, BOMLine,
            WorkOrder, CostLine, ScrapRecord, MrpSuggestion,
            StockCount, StockCountLine,
            Worker, WorkerAdvance, Attendance, PayrollRun, PayrollLine,
            Customer, CustomerOrder, CustomerPayment,
            Quotation, QuotationLine, Invoice, InvoiceLine,
            Supplier, PurchaseInvoice, PurchaseInvoiceLine,
            CustomerAsset, FieldServiceOrder, ServiceAssignment, ServiceVisit,
            # v8/v9 models
            PurchaseOrder, PurchaseOrderLine,
            ChartOfAccount, AccountingEntry, AccountingEntryLine,
            CustomerPaymentAllocation,
            ProductionSchedule, QualityInspection, SalesOrderWorkOrder,
            WorkOrderStage, StockBatch,
            Treasury, TreasuryCategory, TreasuryTransaction,
            SupplyContract, SupplyContractLine,
            CreditNote, CreditNoteLine,
            WorkCompletionCert, WorkCompletionItem,
            WarrantyCertificate,
        )

        today = date.today()
        def d(n): return today - timedelta(days=n)

        # ── 0. TENANT ─────────────────────────────────────────────────
        tenant = Tenant(
            name="الشركة المصرية لتصنيع معدات الأعلاف",
            slug="warsha-main",
            plan="pro",
            is_active=True,
        )
        db.add(tenant); await db.flush()
        TID = tenant.id
        print(f"✅ Tenant: {tenant.name} (id={TID})")

        # ── 1. USERS ──────────────────────────────────────────────────
        users = [
            User(tenant_id=TID, username="admin",       password_hash=_hash("admin123"),      full_name="مدير النظام",       role="admin",      is_active=True),
            User(tenant_id=TID, username="accountant",  password_hash=_hash("accountant123"), full_name="أحمد المحاسب",       role="accountant", is_active=True),
            User(tenant_id=TID, username="production",  password_hash=_hash("production123"), full_name="محمد مشرف الإنتاج",  role="production", is_active=True),
            User(tenant_id=TID, username="viewer",      password_hash=_hash("viewer123"),     full_name="سارة المراقبة",       role="viewer",     is_active=True),
            User(tenant_id=TID, username="storekeeper", password_hash=_hash("store123"),      full_name="علي أمين المخزن",     role="viewer",     is_active=True),
        ]
        db.add_all(users); await db.flush()
        print(f"✅ Users: {len(users)}")

        # ── 2. MATERIAL CATEGORIES ────────────────────────────────────
        cats = [
            MaterialCategory(tenant_id=TID, name_ar="فولاذ وألواح",      name_en="Steel & Plates"),
            MaterialCategory(tenant_id=TID, name_ar="هيدروليك",          name_en="Hydraulics"),
            MaterialCategory(tenant_id=TID, name_ar="كهرباء وتحكم",      name_en="Electrical & Control"),
            MaterialCategory(tenant_id=TID, name_ar="مسامير وبراغي",     name_en="Fasteners"),
            MaterialCategory(tenant_id=TID, name_ar="مواد استهلاكية",    name_en="Consumables"),
            MaterialCategory(tenant_id=TID, name_ar="تروس وناقلات حركة", name_en="Gears & Drives"),
            MaterialCategory(tenant_id=TID, name_ar="شاشات وتغذية",      name_en="Screens & Feeders"),
            MaterialCategory(tenant_id=TID, name_ar="مواد خام مساعدة",   name_en="Raw Auxiliaries"),
        ]
        db.add_all(cats); await db.flush()
        cat_steel, cat_hydro, cat_elec, cat_fast, cat_cons, cat_gears, cat_screen, cat_raw = cats
        print(f"✅ Categories: {len(cats)}")

        # ── 3. SUPPLIERS ──────────────────────────────────────────────
        suppliers = [
            Supplier(tenant_id=TID, code="SUP-001", name="شركة الفولاذ المتحدة",   phone="0223456789", address="المنطقة الصناعية، العاشر من رمضان", tax_id="123456789", payment_terms="30 يوم من الاستلام", balance=dec("0"), is_active=True),
            Supplier(tenant_id=TID, code="SUP-002", name="Parker Hannifin Egypt",   phone="0234567890", address="مدينة نصر، القاهرة",                tax_id="987654321", payment_terms="60 يوم",             balance=dec("0"), is_active=True),
            Supplier(tenant_id=TID, code="SUP-003", name="وكيل سيمنز مصر",          phone="0245678901", address="طريق الجيش، الزيتون، القاهرة",      tax_id="456789123", payment_terms="نقدي",               balance=dec("0"), is_active=True),
            Supplier(tenant_id=TID, code="SUP-004", name="مصنع الشاشات الوطني",     phone="0256789012", address="ش الصناعة، شبرا الخيمة",            payment_terms="15 يوم",                                   balance=dec("0"), is_active=True),
            Supplier(tenant_id=TID, code="SUP-005", name="Delta Electronics Egypt", phone="0267890123", address="مدينة 6 أكتوبر، الجيزة",            tax_id="789123456", payment_terms="45 يوم",             balance=dec("0"), is_active=True),
        ]
        db.add_all(suppliers); await db.flush()
        sup_steel, sup_parker, sup_siemens, sup_screen, sup_delta = suppliers
        print(f"✅ Suppliers: {len(suppliers)}")

        # ── 4. MATERIALS ──────────────────────────────────────────────
        def mat(code, name_ar, name_en, unit, uc, sq, rl, cat, sid=None,
                lpp=None, ltd=None, moq=None, plate=False, thick=None, density=None, kind="general"):
            return Material(tenant_id=TID, 
                code=code, name_ar=name_ar, name_en=name_en, unit=unit,
                unit_cost=dec(uc), stock_qty=dec(sq), reorder_level=dec(rl),
                category_id=cat.id, supplier_id=sid,
                last_purchase_price=dec(lpp), lead_time_days=ltd, min_order_qty=dec(moq),
                is_plate=plate, thickness_mm=dec(thick), density_kg_m2=dec(density),
                material_kind=kind,
            )

        st37   = mat("ST37-6MM",   "لوح فولاذ ST-37 سماكة 6mm",   "Steel Plate ST37 6mm",       "kg",   "16.00", "3000","400",  cat_steel, sup_steel.id,  "16.00",7, "500",  True,"6",  "47.10","plate")
        st52   = mat("ST52-10MM",  "لوح فولاذ ST-52 سماكة 10mm",  "Steel Plate ST52 10mm",      "kg",   "18.50", "4200","500",  cat_steel, sup_steel.id,  "18.50",7, "1000", True,"10", "78.50","plate")
        st37_3 = mat("ST37-3MM",   "لوح فولاذ ST-37 سماكة 3mm",   "Steel Plate ST37 3mm",       "kg",   "15.00", "1500","300",  cat_steel, sup_steel.id,  "15.00",7, "300",  True,"3",  "23.55","plate")
        ss304  = mat("SS304-2MM",  "لوح استانلس 304 سماكة 2mm",   "Stainless Steel 304 2mm",    "kg",   "95.00", "800", "100",  cat_steel, None,          "95.00",14,None,   True,"2",  "15.76","plate")
        pipe60 = mat("PIPE-60",    "أنبوب فولاذي 60mm",            "Steel Pipe 60mm",            "m",    "85.00", "150", "20",   cat_steel, sup_steel.id,  "82.00",5)
        pipe40 = mat("PIPE-40",    "أنبوب فولاذي 40mm",            "Steel Pipe 40mm",            "m",    "55.00", "200", "30",   cat_steel, sup_steel.id,  "53.00",5)
        sqbar  = mat("SQ-BAR40",   "مقطع مربع 40×40mm",            "Square Bar 40x40mm",         "m",    "55.00", "200", "30",   cat_steel, sup_steel.id)
        angle  = mat("ANGLE-50",   "زاوية حديدية 50×50mm",         "Angle Iron 50x50mm",         "m",    "42.00", "320", "50",   cat_steel)
        ch100  = mat("CH-100",     "قناة حديد C-100",              "C-Channel 100mm",            "m",   "120.00", "90",  "15",   cat_steel)
        rod20  = mat("ROD-20",     "سيخ دائري 20mm",               "Round Rod 20mm",             "m",    "38.00", "250", "40",   cat_steel)
        hpump  = mat("HYD-P11",    "طلمبة هيدروليك 11KW",          "Hydraulic Pump 11KW",        "pcs","6500.00", "5",   "1",    cat_hydro, sup_parker.id,"6200.00",21,"1")
        hcyl   = mat("HYD-C80",    "أسطوانة هيدروليك 80mm",        "Hydraulic Cylinder 80mm",    "pcs","2800.00", "8",   "2",    cat_hydro, sup_parker.id,"2700.00",14)
        hyd_oil= mat("HYD-OIL46",  "زيت هيدروليك VG46",            "Hydraulic Oil VG46",         "لتر",  "28.00", "500", "100",  cat_hydro, None,          "26.00")
        hyd_hose=mat("HYD-HOSE1",  "خرطوم هيدروليك 1 بوصة",       "Hydraulic Hose 1 inch",      "m",    "65.00", "80",  "15",   cat_hydro, sup_parker.id)
        mot15  = mat("MOT-15KW",   "موتور كهربائي 15KW",           "Electric Motor 15KW",        "pcs","7200.00", "6",   "1",    cat_elec,  sup_siemens.id,"7000.00",30)
        mot75  = mat("MOT-7KW",    "موتور كهربائي 7.5KW",          "Electric Motor 7.5KW",       "pcs","4100.00", "8",   "2",    cat_elec,  sup_siemens.id,"3900.00",30)
        mot3   = mat("MOT-3KW",    "موتور كهربائي 3KW",            "Electric Motor 3KW",         "pcs","2200.00", "5",   "1",    cat_elec,  sup_siemens.id)
        vfd    = mat("VFD-15",     "انفرتر تحكم سرعة 15KW",        "VFD Inverter 15KW",          "pcs","5500.00", "4",   "1",    cat_elec,  sup_delta.id,  "5300.00",21)
        plc    = mat("PLC-S7",     "وحدة تحكم PLC Siemens S7-1200","PLC Siemens S7-1200",         "pcs","8500.00", "3",   "1",    cat_elec,  sup_siemens.id)
        sensor = mat("SENS-PRX",   "حساس تقاربي M18",              "Proximity Sensor M18",       "pcs",  "120.00","30",  "5",    cat_elec)
        cb     = mat("CB-100A",    "قاطع كهربائي 100A",            "Circuit Breaker 100A",       "pcs",  "450.00","10",  "2",    cat_elec)
        gb20   = mat("GB-1-20",    "علبة تروس نسبة 1:20",          "Gearbox Ratio 1:20",         "pcs","3800.00", "6",   "1",    cat_gears)
        gb40   = mat("GB-1-40",    "علبة تروس نسبة 1:40",          "Gearbox Ratio 1:40",         "pcs","4500.00", "4",   "1",    cat_gears)
        belt   = mat("BELT-B80",   "سير نقل حركة B-80",            "V-Belt B80",                 "pcs",   "45.00","50",  "10",   cat_gears)
        coup   = mat("CUP-65",     "وصلة مرنة 65mm",               "Flexible Coupling 65mm",     "pcs",  "380.00","12",  "2",    cat_gears)
        brg1   = mat("BRG-6305",   "بلي SKF-6305",                  "Bearing SKF 6305",           "pcs",   "85.00","40",  "8",    cat_gears)
        brg2   = mat("BRG-6208",   "بلي SKF-6208",                  "Bearing SKF 6208",           "pcs",   "65.00","30",  "6",    cat_gears)
        scr3   = mat("SCR-3MM",    "شاشة تصفية 3mm",               "Sieve Screen 3mm",           "pcs","1200.00", "12",  "3",    cat_screen,sup_screen.id, "1150.00")
        scr5   = mat("SCR-5MM",    "شاشة تصفية 5mm",               "Sieve Screen 5mm",           "pcs","1200.00", "10",  "3",    cat_screen,sup_screen.id, "1150.00")
        scr8   = mat("SCR-8MM",    "شاشة تصفية 8mm",               "Sieve Screen 8mm",           "pcs","1200.00", "8",   "2",    cat_screen,sup_screen.id)
        screw  = mat("SCREW-FED",  "ملولب تغذية قطر 200mm",        "Screw Feeder D200",          "m",    "450.00","15",  "3",    cat_screen)
        blt16  = mat("BOLT-M16",   "مسمار M16 استانلس 8.8",        "Hex Bolt M16 SS 8.8",        "pcs",    "8.00","2000","300",  cat_fast)
        blt12  = mat("BOLT-M12",   "مسمار M12 استانلس 8.8",        "Hex Bolt M12 SS 8.8",        "pcs",    "4.50","3000","500",  cat_fast)
        nut16  = mat("NUT-M16",    "صامولة M16",                   "Hex Nut M16",                "pcs",    "3.50","2000","300",  cat_fast)
        anch   = mat("ANCHOR-M20", "بسيمة تثبيت M20",              "Anchor Bolt M20",            "pcs",   "15.00","500", "100",  cat_fast)
        wr     = mat("WELD-7018",  "سلك لحام E7018 قطر 3.2mm",     "Welding Rod E7018 3.2mm",    "kg",    "35.00","200", "40",   cat_cons)
        mig    = mat("MIG-08",     "سلك ميج 0.8mm",                "MIG Wire 0.8mm",             "kg",    "55.00","80",  "15",   cat_cons)
        gdisc  = mat("DISC-180",   "قرص جلخ 180mm",                "Grinding Disc 180mm",        "pcs",   "18.00","200", "50",   cat_cons)
        cdisc  = mat("DISC-CUT",   "قرص قطع 230mm",                "Cutting Disc 230mm",         "pcs",   "22.00","150", "30",   cat_cons)
        pnt_g  = mat("PAINT-GRY",  "دهان رمادي أنتي كوروجن",       "Gray Anti-Corrosion Paint",  "kg",    "45.00","150", "30",   cat_cons)
        pnt_gr = mat("PAINT-GRN",  "دهان أخضر غطاء نهائي",         "Green Top Coat Paint",       "kg",    "55.00","80",  "20",   cat_cons)
        gas    = mat("GAS-CO2",    "أسطوانة CO2 لحام",             "CO2 Welding Gas Cylinder",   "pcs",  "180.00","10",  "3",    cat_cons)

        mats = [st37,st52,st37_3,ss304,pipe60,pipe40,sqbar,angle,ch100,rod20,
                hpump,hcyl,hyd_oil,hyd_hose,mot15,mot75,mot3,vfd,plc,sensor,cb,
                gb20,gb40,belt,coup,brg1,brg2,scr3,scr5,scr8,screw,
                blt16,blt12,nut16,anch,wr,mig,gdisc,cdisc,pnt_g,pnt_gr,gas]
        db.add_all(mats); await db.flush()
        print(f"✅ Materials: {len(mats)}")

        # ── 5. STOCK MOVEMENTS ────────────────────────────────────────
        movements = [
            StockMovement(tenant_id=TID, material_id=st37.id,   movement_type="in",  qty=dec("3000"),unit_cost=dec("16.00"),  total_cost=dec("48000"),  reference="PO-001",movement_date=d(120),notes="شراء — فولاذ المتحدة"),
            StockMovement(tenant_id=TID, material_id=st52.id,   movement_type="in",  qty=dec("4200"),unit_cost=dec("18.50"),  total_cost=dec("77700"),  reference="PO-001",movement_date=d(120),notes="شراء — فولاذ المتحدة"),
            StockMovement(tenant_id=TID, material_id=st37_3.id, movement_type="in",  qty=dec("1500"),unit_cost=dec("15.00"),  total_cost=dec("22500"),  reference="PO-001",movement_date=d(120),notes="شراء"),
            StockMovement(tenant_id=TID, material_id=ss304.id,  movement_type="in",  qty=dec("800"), unit_cost=dec("95.00"),  total_cost=dec("76000"),  reference="PO-002",movement_date=d(110),notes="شراء استانلس"),
            StockMovement(tenant_id=TID, material_id=mot15.id,  movement_type="in",  qty=dec("6"),   unit_cost=dec("7200.00"),total_cost=dec("43200"),  reference="PO-003",movement_date=d(100),notes="شراء سيمنز"),
            StockMovement(tenant_id=TID, material_id=mot75.id,  movement_type="in",  qty=dec("8"),   unit_cost=dec("4100.00"),total_cost=dec("32800"),  reference="PO-003",movement_date=d(100),notes="شراء سيمنز"),
            StockMovement(tenant_id=TID, material_id=hpump.id,  movement_type="in",  qty=dec("5"),   unit_cost=dec("6500.00"),total_cost=dec("32500"),  reference="PO-004",movement_date=d(90), notes="شراء Parker"),
            StockMovement(tenant_id=TID, material_id=hcyl.id,   movement_type="in",  qty=dec("8"),   unit_cost=dec("2800.00"),total_cost=dec("22400"),  reference="PO-004",movement_date=d(90), notes="شراء Parker"),
            StockMovement(tenant_id=TID, material_id=scr3.id,   movement_type="in",  qty=dec("12"),  unit_cost=dec("1200.00"),total_cost=dec("14400"),  reference="PO-005",movement_date=d(80), notes="شراء شاشات"),
            StockMovement(tenant_id=TID, material_id=scr5.id,   movement_type="in",  qty=dec("10"),  unit_cost=dec("1200.00"),total_cost=dec("12000"),  reference="PO-005",movement_date=d(80), notes="شراء شاشات"),
            StockMovement(tenant_id=TID, material_id=wr.id,     movement_type="in",  qty=dec("200"), unit_cost=dec("35.00"),  total_cost=dec("7000"),   reference="PO-006",movement_date=d(60), notes="مواد استهلاكية"),
            StockMovement(tenant_id=TID, material_id=mig.id,    movement_type="in",  qty=dec("80"),  unit_cost=dec("55.00"),  total_cost=dec("4400"),   reference="PO-006",movement_date=d(60), notes="مواد استهلاكية"),
            StockMovement(tenant_id=TID, material_id=gdisc.id,  movement_type="in",  qty=dec("200"), unit_cost=dec("18.00"),  total_cost=dec("3600"),   reference="PO-006",movement_date=d(60), notes="مواد استهلاكية"),
            StockMovement(tenant_id=TID, material_id=vfd.id,    movement_type="in",  qty=dec("4"),   unit_cost=dec("5500.00"),total_cost=dec("22000"),  reference="PO-007",movement_date=d(50), notes="Delta Electronics"),
            StockMovement(tenant_id=TID, material_id=plc.id,    movement_type="in",  qty=dec("3"),   unit_cost=dec("8500.00"),total_cost=dec("25500"),  reference="PO-007",movement_date=d(50), notes="سيمنز PLC"),
            StockMovement(tenant_id=TID, material_id=st52.id,   movement_type="out", qty=dec("420"), unit_cost=dec("18.50"),  total_cost=dec("7770"),   reference="WO-001",movement_date=d(70), notes="صرف إنتاج WO-001",  destination="workshop",withdrawal_unit="weight"),
            StockMovement(tenant_id=TID, material_id=st37.id,   movement_type="out", qty=dec("220"), unit_cost=dec("16.00"),  total_cost=dec("3520"),   reference="WO-001",movement_date=d(68), notes="صرف إنتاج WO-001",  destination="workshop",withdrawal_unit="weight"),
            StockMovement(tenant_id=TID, material_id=mot15.id,  movement_type="out", qty=dec("1"),   unit_cost=dec("7200.00"),total_cost=dec("7200"),   reference="WO-001",movement_date=d(65), notes="موتور WO-001",       destination="workshop"),
            StockMovement(tenant_id=TID, material_id=vfd.id,    movement_type="out", qty=dec("1"),   unit_cost=dec("5500.00"),total_cost=dec("5500"),   reference="WO-001",movement_date=d(65), notes="انفرتر WO-001",      destination="workshop"),
            StockMovement(tenant_id=TID, material_id=wr.id,     movement_type="out", qty=dec("30"),  unit_cost=dec("35.00"),  total_cost=dec("1050"),   reference="WO-001",movement_date=d(60), notes="لحام WO-001",        destination="workshop"),
            StockMovement(tenant_id=TID, material_id=mig.id,    movement_type="out", qty=dec("15"),  unit_cost=dec("55.00"),  total_cost=dec("825"),    reference="WO-001",movement_date=d(60), notes="ميج WO-001",         destination="workshop"),
            StockMovement(tenant_id=TID, material_id=gdisc.id,  movement_type="out", qty=dec("40"),  unit_cost=dec("18.00"),  total_cost=dec("720"),    reference="WO-001",movement_date=d(55), notes="جلخ WO-001",         destination="workshop"),
            StockMovement(tenant_id=TID, material_id=scr3.id,   movement_type="out", qty=dec("4"),   unit_cost=dec("1200.00"),total_cost=dec("4800"),   reference="ORD-003",movement_date=d(45),notes="شاشات 3mm عميل C-004",destination="customer"),
            StockMovement(tenant_id=TID, material_id=scr5.id,   movement_type="out", qty=dec("2"),   unit_cost=dec("1200.00"),total_cost=dec("2400"),   reference="ORD-003",movement_date=d(45),notes="شاشات 5mm عميل C-004",destination="customer"),
        ]
        db.add_all(movements); await db.flush()
        print(f"✅ Stock Movements: {len(movements)}")

        # ── 6. PURCHASE INVOICES ──────────────────────────────────────
        pi1=PurchaseInvoice(tenant_id=TID, code="PI-2024-001",supplier_id=sup_steel.id,  supplier_name="شركة الفولاذ المتحدة",  date=d(120),due_date=d(90), status="paid",    subtotal=dec("148200"),tax_pct=dec("14"),tax_amount=dec("20748"),discount_amt=dec("0"),   total=dec("168948"),paid_amount=dec("168948"),notes="فولاذ — دفعة يناير")
        pi2=PurchaseInvoice(tenant_id=TID, code="PI-2024-002",supplier_id=sup_parker.id, supplier_name="Parker Hannifin Egypt", date=d(90), due_date=d(30), status="partial", subtotal=dec("54900"), tax_pct=dec("14"),tax_amount=dec("7686"), discount_amt=dec("0"),   total=dec("62586"), paid_amount=dec("30000"),notes="طلمبات وأسطوانات هيدروليكية")
        pi3=PurchaseInvoice(tenant_id=TID, code="PI-2024-003",supplier_id=sup_siemens.id,supplier_name="وكيل سيمنز مصر",        date=d(100),due_date=d(70), status="paid",    subtotal=dec("76000"), tax_pct=dec("14"),tax_amount=dec("10640"),discount_amt=dec("0"),   total=dec("86640"), paid_amount=dec("86640"),notes="موتورات كهربائية")
        pi4=PurchaseInvoice(tenant_id=TID, code="PI-2024-004",supplier_id=sup_screen.id, supplier_name="مصنع الشاشات الوطني",   date=d(80), due_date=d(50), status="received",subtotal=dec("26400"), tax_pct=dec("0"), tax_amount=dec("0"),   discount_amt=dec("0"),   total=dec("26400"), paid_amount=dec("0"),    notes="شاشات تصفية")
        pi5=PurchaseInvoice(tenant_id=TID, code="PI-2024-005",supplier_id=sup_delta.id,  supplier_name="Delta Electronics Egypt",date=d(50),due_date=d(5),  status="received",subtotal=dec("47500"), tax_pct=dec("14"),tax_amount=dec("6650"), discount_amt=dec("2500"),total=dec("51650"), paid_amount=dec("0"),    notes="انفرترات وPLC")
        db.add_all([pi1,pi2,pi3,pi4,pi5]); await db.flush()

        pi_lines=[
            PurchaseInvoiceLine(tenant_id=TID, invoice_id=pi1.id,material_id=st37.id,  description="لوح ST-37 6mm",  qty=dec("3000"),unit="kg", unit_cost=dec("16.00"),  total_cost=dec("48000"), update_stock=False),
            PurchaseInvoiceLine(tenant_id=TID, invoice_id=pi1.id,material_id=st52.id,  description="لوح ST-52 10mm", qty=dec("4200"),unit="kg", unit_cost=dec("18.50"),  total_cost=dec("77700"), update_stock=False),
            PurchaseInvoiceLine(tenant_id=TID, invoice_id=pi1.id,material_id=st37_3.id,description="لوح ST-37 3mm",  qty=dec("1500"),unit="kg", unit_cost=dec("15.00"),  total_cost=dec("22500"), update_stock=False),
            PurchaseInvoiceLine(tenant_id=TID, invoice_id=pi2.id,material_id=hpump.id, description="طلمبة هيدروليك", qty=dec("5"),  unit="pcs",unit_cost=dec("6500.00"),total_cost=dec("32500"), update_stock=False),
            PurchaseInvoiceLine(tenant_id=TID, invoice_id=pi2.id,material_id=hcyl.id,  description="أسطوانة هيدروليك",qty=dec("8"), unit="pcs",unit_cost=dec("2800.00"),total_cost=dec("22400"), update_stock=False),
            PurchaseInvoiceLine(tenant_id=TID, invoice_id=pi3.id,material_id=mot15.id, description="موتور 15KW",     qty=dec("6"),  unit="pcs",unit_cost=dec("7200.00"),total_cost=dec("43200"), update_stock=False),
            PurchaseInvoiceLine(tenant_id=TID, invoice_id=pi3.id,material_id=mot75.id, description="موتور 7.5KW",    qty=dec("8"),  unit="pcs",unit_cost=dec("4100.00"),total_cost=dec("32800"), update_stock=False),
            PurchaseInvoiceLine(tenant_id=TID, invoice_id=pi4.id,material_id=scr3.id,  description="شاشة 3mm",       qty=dec("12"), unit="pcs",unit_cost=dec("1200.00"),total_cost=dec("14400"), update_stock=False),
            PurchaseInvoiceLine(tenant_id=TID, invoice_id=pi4.id,material_id=scr5.id,  description="شاشة 5mm",       qty=dec("10"), unit="pcs",unit_cost=dec("1200.00"),total_cost=dec("12000"), update_stock=False),
            PurchaseInvoiceLine(tenant_id=TID, invoice_id=pi5.id,material_id=vfd.id,   description="انفرتر 15KW",    qty=dec("4"),  unit="pcs",unit_cost=dec("5500.00"),total_cost=dec("22000"), update_stock=False),
            PurchaseInvoiceLine(tenant_id=TID, invoice_id=pi5.id,material_id=plc.id,   description="PLC S7-1200",    qty=dec("3"),  unit="pcs",unit_cost=dec("8500.00"),total_cost=dec("25500"), update_stock=False),
        ]
        db.add_all(pi_lines)
        sup_parker.balance=pi2.total-pi2.paid_amount
        sup_screen.balance=pi4.total
        sup_delta.balance =pi5.total
        await db.flush()
        print(f"✅ Purchase Invoices: 5  (lines: {len(pi_lines)})")

        # ── 7. EQUIPMENT + BOM ────────────────────────────────────────
        fg=Equipment(tenant_id=TID, code="FG-500", name_ar="مجرشة أعلاف FG-500",  name_en="Feed Grinder FG-500",  description="مجرشة أعلاف 500 كجم/ساعة — موتور 15KW انفرتر تحكم سرعة علبة تروس 1:20 شاشات متعددة", weight_kg=dec("1200"),is_active=True)
        mx=Equipment(tenant_id=TID, code="MX-200", name_ar="خلاط أعلاف MX-200",   name_en="Feed Mixer MX-200",    description="خلاط أعلاف سعة 2 طن — بطانة داخلية استانلس موتور 7.5KW",                               weight_kg=dec("1850"),is_active=True)
        hp=Equipment(tenant_id=TID, code="HP-50T", name_ar="مكبس هيدروليكي 50 طن",name_en="Hydraulic Press 50T",  description="مكبس هيدروليكي 50 طن للكبس الساخن والبارد مع نظام هيدروليك مكتمل",                     weight_kg=dec("3200"),is_active=True)
        cn=Equipment(tenant_id=TID, code="CNV-003",name_ar="ناقل أعلاف 10 متر",   name_en="Feed Conveyor 10m",    description="ناقل حلزوني بسرعة متغيرة طول 10م موتور 3KW",                                              weight_kg=dec("420"), is_active=True)
        db.add_all([fg,mx,hp,cn]); await db.flush()

        dims=[
            EquipmentDimension(tenant_id=TID, equipment_id=fg.id,dim_key="length_mm",   dim_value="2200",unit="mm"),
            EquipmentDimension(tenant_id=TID, equipment_id=fg.id,dim_key="width_mm",    dim_value="1400",unit="mm"),
            EquipmentDimension(tenant_id=TID, equipment_id=fg.id,dim_key="height_mm",   dim_value="1800",unit="mm"),
            EquipmentDimension(tenant_id=TID, equipment_id=fg.id,dim_key="capacity_kgh",dim_value="500", unit="kg/h"),
            EquipmentDimension(tenant_id=TID, equipment_id=mx.id,dim_key="length_mm",   dim_value="3200",unit="mm"),
            EquipmentDimension(tenant_id=TID, equipment_id=mx.id,dim_key="capacity_ton",dim_value="2",   unit="ton"),
            EquipmentDimension(tenant_id=TID, equipment_id=hp.id,dim_key="force_ton",   dim_value="50",  unit="ton"),
            EquipmentDimension(tenant_id=TID, equipment_id=hp.id,dim_key="stroke_mm",   dim_value="400", unit="mm"),
            EquipmentDimension(tenant_id=TID, equipment_id=cn.id,dim_key="length_m",    dim_value="10",  unit="m"),
            EquipmentDimension(tenant_id=TID, equipment_id=cn.id,dim_key="diameter_mm", dim_value="200", unit="mm"),
        ]
        db.add_all(dims)

        bom=[
            BOMLine(tenant_id=TID, equipment_id=fg.id,material_id=st52.id, qty=dec("680"), unit_cost=dec("18.50"),  total_cost=dec("12580"),notes="هيكل رئيسي وغرفة الطحن"),
            BOMLine(tenant_id=TID, equipment_id=fg.id,material_id=st37.id, qty=dec("200"), unit_cost=dec("16.00"),  total_cost=dec("3200"), notes="هيكل خارجي وحوامل"),
            BOMLine(tenant_id=TID, equipment_id=fg.id,material_id=mot15.id,qty=dec("1"),   unit_cost=dec("7200.00"),total_cost=dec("7200"), notes="محرك طحن رئيسي"),
            BOMLine(tenant_id=TID, equipment_id=fg.id,material_id=vfd.id,  qty=dec("1"),   unit_cost=dec("5500.00"),total_cost=dec("5500"), notes="تحكم سرعة المحرك"),
            BOMLine(tenant_id=TID, equipment_id=fg.id,material_id=gb20.id, qty=dec("1"),   unit_cost=dec("3800.00"),total_cost=dec("3800"), notes="علبة تروس 1:20"),
            BOMLine(tenant_id=TID, equipment_id=fg.id,material_id=scr3.id, qty=dec("2"),   unit_cost=dec("1200.00"),total_cost=dec("2400"), notes="شاشات تصفية 3mm"),
            BOMLine(tenant_id=TID, equipment_id=fg.id,material_id=brg1.id, qty=dec("4"),   unit_cost=dec("85.00"),  total_cost=dec("340"),  notes="بلي محاور"),
            BOMLine(tenant_id=TID, equipment_id=fg.id,material_id=wr.id,   qty=dec("25"),  unit_cost=dec("35.00"),  total_cost=dec("875"),  notes="سلك لحام"),
            BOMLine(tenant_id=TID, equipment_id=fg.id,material_id=pnt_g.id,qty=dec("12"),  unit_cost=dec("45.00"),  total_cost=dec("540"),  notes="دهان أنتي كوروجن"),
            BOMLine(tenant_id=TID, equipment_id=fg.id,material_id=blt16.id,qty=dec("80"),  unit_cost=dec("8.00"),   total_cost=dec("640"),  notes="مسامير تجميع"),
            BOMLine(tenant_id=TID, equipment_id=mx.id,material_id=st52.id, qty=dec("850"), unit_cost=dec("18.50"),  total_cost=dec("15725"),notes="وعاء الخلط والهيكل"),
            BOMLine(tenant_id=TID, equipment_id=mx.id,material_id=ss304.id,qty=dec("180"), unit_cost=dec("95.00"),  total_cost=dec("17100"),notes="بطانة داخلية استانلس"),
            BOMLine(tenant_id=TID, equipment_id=mx.id,material_id=mot75.id,qty=dec("1"),   unit_cost=dec("4100.00"),total_cost=dec("4100"), notes="محرك الخلط"),
            BOMLine(tenant_id=TID, equipment_id=mx.id,material_id=gb40.id, qty=dec("1"),   unit_cost=dec("4500.00"),total_cost=dec("4500"), notes="علبة تروس 1:40"),
            BOMLine(tenant_id=TID, equipment_id=mx.id,material_id=screw.id,qty=dec("3"),   unit_cost=dec("450.00"), total_cost=dec("1350"), notes="ملولب التحريك"),
            BOMLine(tenant_id=TID, equipment_id=hp.id,material_id=st52.id, qty=dec("1200"),unit_cost=dec("18.50"),  total_cost=dec("22200"),notes="الهيكل الرئيسي والإطار"),
            BOMLine(tenant_id=TID, equipment_id=hp.id,material_id=hpump.id,qty=dec("1"),   unit_cost=dec("6500.00"),total_cost=dec("6500"), notes="طلمبة هيدروليك رئيسية"),
            BOMLine(tenant_id=TID, equipment_id=hp.id,material_id=hcyl.id, qty=dec("2"),   unit_cost=dec("2800.00"),total_cost=dec("5600"), notes="أسطوانتان هيدروليكيتان"),
            BOMLine(tenant_id=TID, equipment_id=hp.id,material_id=hyd_oil.id,qty=dec("80"),unit_cost=dec("28.00"),  total_cost=dec("2240"), notes="زيت هيدروليك"),
            BOMLine(tenant_id=TID, equipment_id=cn.id,material_id=st37.id, qty=dec("180"), unit_cost=dec("16.00"),  total_cost=dec("2880"), notes="هيكل الناقل"),
            BOMLine(tenant_id=TID, equipment_id=cn.id,material_id=pipe60.id,qty=dec("10"), unit_cost=dec("85.00"),  total_cost=dec("850"),  notes="أنابيب المحور"),
            BOMLine(tenant_id=TID, equipment_id=cn.id,material_id=mot3.id, qty=dec("1"),   unit_cost=dec("2200.00"),total_cost=dec("2200"), notes="موتور الناقل"),
            BOMLine(tenant_id=TID, equipment_id=cn.id,material_id=brg2.id, qty=dec("4"),   unit_cost=dec("65.00"),  total_cost=dec("260"),  notes="بلي الناقل"),
        ]
        db.add_all(bom); await db.flush()
        print(f"✅ Equipment: 4  |  BOM Lines: {len(bom)}")

        # ── 8. WORKERS ────────────────────────────────────────────────
        workers=[
            Worker(tenant_id=TID, code="W-001",name="خالد محمود إبراهيم", job_title="لحام وتصنيع",    base_weekly_wage=dec("1680"),daily_wage=dec("280"),national_id="12345678901234",phone="0111111111",hire_date=date(2020,3,1), is_active=True),
            Worker(tenant_id=TID, code="W-002",name="محمد أحمد سالم",      job_title="ميكانيكا وتجميع",base_weekly_wage=dec("1560"),daily_wage=dec("260"),national_id="23456789012345",phone="0122222222",hire_date=date(2019,6,15),is_active=True),
            Worker(tenant_id=TID, code="W-003",name="سامي حسن علي",        job_title="كهرباء وتحكم",   base_weekly_wage=dec("1800"),daily_wage=dec("300"),national_id="34567890123456",phone="0133333333",hire_date=date(2021,1,10),is_active=True),
            Worker(tenant_id=TID, code="W-004",name="عمر عبدالله فتحي",    job_title="لحام",            base_weekly_wage=dec("1500"),daily_wage=dec("250"),national_id="45678901234567",phone="0144444444",hire_date=date(2022,5,20),is_active=True),
            Worker(tenant_id=TID, code="W-005",name="كريم إبراهيم منصور",  job_title="خدمة ميدانية",   base_weekly_wage=dec("1740"),daily_wage=dec("290"),national_id="56789012345678",phone="0155555555",hire_date=date(2021,8,1), is_active=True),
            Worker(tenant_id=TID, code="W-006",name="هشام علي محمد",       job_title="خدمة ميدانية",   base_weekly_wage=dec("1620"),daily_wage=dec("270"),national_id="67890123456789",phone="0166666666",hire_date=date(2022,2,14),is_active=True),
            Worker(tenant_id=TID, code="W-007",name="طارق سعيد عبدالرحمن",job_title="تشغيل ومتابعة",  base_weekly_wage=dec("1440"),daily_wage=dec("240"),national_id="78901234567890",phone="0177777777",hire_date=date(2023,4,1), is_active=True),
            Worker(tenant_id=TID, code="W-008",name="أشرف جمال الدين",     job_title="صيانة وإصلاح",   base_weekly_wage=dec("1590"),daily_wage=dec("265"),national_id="89012345678901",phone="0188888888",hire_date=date(2020,11,1),is_active=True),
        ]
        db.add_all(workers); await db.flush()
        w1,w2,w3,w4,w5,w6,w7,w8=workers

        db.add_all([
            WorkerAdvance(tenant_id=TID, worker_id=w1.id,amount=dec("500"), advance_type="advance",   date=d(30),notes="سلفة شخصية"),
            WorkerAdvance(tenant_id=TID, worker_id=w3.id,amount=dec("300"), advance_type="bonus",     date=d(15),notes="مكافأة أداء"),
            WorkerAdvance(tenant_id=TID, worker_id=w4.id,amount=dec("200"), advance_type="deduction", date=d(10),notes="خصم تأخير"),
            WorkerAdvance(tenant_id=TID, worker_id=w5.id,amount=dec("1000"),advance_type="advance",   date=d(20),notes="سلفة ميدانية"),
        ])

        att_records=[]
        for wk in workers:
            for day_off in range(26):
                att_date=today-timedelta(days=day_off)
                if att_date.weekday()==4: continue
                status="present"; overtime=dec("0")
                if day_off==3 and wk.code in["W-004","W-007"]: status="absent"
                elif day_off==1 and wk.code=="W-002": status="late"
                elif day_off==5 and wk.code in["W-001","W-003"]: overtime=dec("2")
                att_records.append(Attendance(tenant_id=TID, worker_id=wk.id,date=att_date,status=status,overtime_hours=overtime))
        db.add_all(att_records)

        pr=PayrollRun(tenant_id=TID, week_start=d(14),week_end=d(8),status="draft",total_gross=dec("0"),total_deductions=dec("0"),total_net=dec("0"))
        db.add(pr); await db.flush()
        pl=[]
        for wk in workers:
            g=wk.daily_wage*6
            bo=dec("300") if wk.code=="W-003" else dec("0")
            de=dec("500") if wk.code=="W-001" else (dec("200") if wk.code=="W-004" else dec("0"))
            pl.append(PayrollLine(tenant_id=TID, payroll_run_id=pr.id,worker_id=wk.id,days_worked=dec("6"),gross_amount=g,bonus=bo,deductions=de,net_amount=g+bo-de))
        db.add_all(pl)
        pr.total_gross=sum(x.gross_amount for x in pl)
        pr.total_net  =sum(x.net_amount   for x in pl)
        pr.total_deductions=pr.total_gross-pr.total_net
        await db.flush()
        print(f"✅ Workers: {len(workers)}  |  Attendance: {len(att_records)}  |  Payroll: 1")

        # ── 9. CUSTOMERS ──────────────────────────────────────────────
        customers=[
            Customer(tenant_id=TID, code="C-001",name="شركة الدلتا للدواجن",    phone="0223456789",address="ش الجمهورية، المنصورة، الدقهلية",       tax_id="111222333",credit_limit=dec("500000"),balance=dec("0")),
            Customer(tenant_id=TID, code="C-002",name="مزرعة النيل للأعلاف",    phone="0234567890",address="طريق الفيوم الصحراوي، الجيزة",            tax_id="222333444",credit_limit=dec("300000"),balance=dec("0")),
            Customer(tenant_id=TID, code="C-003",name="مصنع الغذاء الحيواني",   phone="0245678901",address="المنطقة الصناعية الثانية، بني سويف",     tax_id="333444555",credit_limit=dec("750000"),balance=dec("0")),
            Customer(tenant_id=TID, code="C-004",name="شركة الأندلس للزراعة",   phone="0256789012",address="طريق الإسكندرية الصحراوي كيلو 52",      tax_id="444555666",credit_limit=dec("200000"),balance=dec("0")),
            Customer(tenant_id=TID, code="C-005",name="مؤسسة الرياض للأعلاف",   phone="0267890123",address="ش الثورة، طنطا، الغربية",                              credit_limit=dec("400000"),balance=dec("0")),
        ]
        db.add_all(customers); await db.flush()
        c1,c2,c3,c4,c5=customers

        orders_co=[
            CustomerOrder(tenant_id=TID, code="ORD-001",customer_id=c1.id,equipment_id=fg.id,description="مجرشة FG-500 مع تركيب وتشغيل",quantity=1,unit_price=dec("250000"),total_price=dec("250000"),status="in_progress",order_date=d(90), delivery_date=d(-30)),
            CustomerOrder(tenant_id=TID, code="ORD-002",customer_id=c2.id,equipment_id=mx.id,description="خلاط أعلاف MX-200 سعة 2 طن",   quantity=1,unit_price=dec("130000"),total_price=dec("130000"),status="pending",    order_date=d(60), delivery_date=d(-60)),
            CustomerOrder(tenant_id=TID, code="ORD-003",customer_id=c4.id,               description="قطع غيار شاشات 3mm و5mm",           quantity=6,unit_price=dec("1500"),  total_price=dec("9000"),  status="delivered", order_date=d(50), delivery_date=d(40)),
            CustomerOrder(tenant_id=TID, code="ORD-004",customer_id=c3.id,equipment_id=hp.id,description="مكبس هيدروليكي 50 طن",          quantity=1,unit_price=dec("320000"),total_price=dec("320000"),status="pending",    order_date=d(30), delivery_date=d(-90)),
            CustomerOrder(tenant_id=TID, code="ORD-005",customer_id=c5.id,equipment_id=cn.id,description="ناقل أعلاف 10م × 2",            quantity=2,unit_price=dec("58000"), total_price=dec("116000"),status="pending",    order_date=d(10), delivery_date=d(-45)),
        ]
        db.add_all(orders_co); await db.flush()
        print(f"✅ Customers: {len(customers)}  |  Orders: {len(orders_co)}")

        # ── 10. QUOTATIONS ────────────────────────────────────────────
        qt1=Quotation(tenant_id=TID, code="QT-2024-001",customer_id=c1.id,date=d(95),valid_until=d(65),status="accepted",tax_pct=dec("14"),discount_pct=dec("0"), subtotal=dec("219298"),tax_amount=dec("30702"),discount_amt=dec("0"),   total=dec("250000"),terms="50% مقدم — 50% تسليم")
        qt2=Quotation(tenant_id=TID, code="QT-2024-002",customer_id=c2.id,date=d(65),valid_until=d(35),status="accepted",tax_pct=dec("14"),discount_pct=dec("5"), subtotal=dec("120000"),tax_amount=dec("16800"),discount_amt=dec("6000"),total=dec("130800"),terms="30 يوم من الاستلام",notes="خصم 5% عميل دائم")
        qt3=Quotation(tenant_id=TID, code="QT-2024-003",customer_id=c3.id,date=d(35),valid_until=d(5), status="sent",    tax_pct=dec("14"),discount_pct=dec("0"), subtotal=dec("280702"),tax_amount=dec("39298"),discount_amt=dec("0"),   total=dec("320000"),terms="30% مقدم — 70% تسليم")
        qt4=Quotation(tenant_id=TID, code="QT-2024-004",customer_id=c5.id,date=d(15),valid_until=d(-15),status="draft",  tax_pct=dec("14"),discount_pct=dec("0"), subtotal=dec("101754"),tax_amount=dec("14246"),discount_amt=dec("0"),   total=dec("116000"),terms="50% مقدم")
        db.add_all([qt1,qt2,qt3,qt4]); await db.flush()

        qt_lines=[
            QuotationLine(tenant_id=TID, quotation_id=qt1.id,description="مجرشة FG-500",          qty=dec("1"),unit="pcs",unit_price=dec("200000"),total_price=dec("200000"),sort_order=1),
            QuotationLine(tenant_id=TID, quotation_id=qt1.id,description="تركيب وتشغيل وتدريب",   qty=dec("1"),unit="pcs",unit_price=dec("19298"), total_price=dec("19298"), sort_order=2),
            QuotationLine(tenant_id=TID, quotation_id=qt2.id,description="خلاط MX-200",            qty=dec("1"),unit="pcs",unit_price=dec("115000"),total_price=dec("115000"),sort_order=1),
            QuotationLine(tenant_id=TID, quotation_id=qt2.id,description="شحن وتركيب",             qty=dec("1"),unit="pcs",unit_price=dec("5000"),  total_price=dec("5000"),  sort_order=2),
            QuotationLine(tenant_id=TID, quotation_id=qt3.id,description="مكبس HP-50T",            qty=dec("1"),unit="pcs",unit_price=dec("280702"),total_price=dec("280702"),sort_order=1),
            QuotationLine(tenant_id=TID, quotation_id=qt4.id,description="ناقل CNV-003 10م",       qty=dec("2"),unit="pcs",unit_price=dec("50877"), total_price=dec("101754"),sort_order=1),
        ]
        db.add_all(qt_lines)
        print(f"✅ Quotations: 4  (lines: {len(qt_lines)})")

        # ── 11. INVOICES ──────────────────────────────────────────────
        inv1=Invoice(tenant_id=TID, code="INV-2024-001",customer_id=c1.id,date=d(88),due_date=d(58),status="paid",   tax_pct=dec("14"),discount_pct=dec("0"),subtotal=dec("109649"),tax_amount=dec("15351"),discount_amt=dec("0"),   total=dec("125000"),paid_amount=dec("125000"),notes="دفعة أولى FG-500")
        inv2=Invoice(tenant_id=TID, code="INV-2024-002",customer_id=c2.id,date=d(58),due_date=d(28),status="unpaid", tax_pct=dec("14"),discount_pct=dec("5"),subtotal=dec("120000"),tax_amount=dec("16800"),discount_amt=dec("6000"),total=dec("130800"),paid_amount=dec("0"),    notes="خلاط MX-200")
        inv3=Invoice(tenant_id=TID, code="INV-2024-003",customer_id=c4.id,date=d(45),due_date=d(15),status="paid",   tax_pct=dec("0"), discount_pct=dec("0"),subtotal=dec("9000"),  tax_amount=dec("0"),   discount_amt=dec("0"),   total=dec("9000"),  paid_amount=dec("9000"),  notes="شاشات ORD-003")
        inv4=Invoice(tenant_id=TID, code="INV-2024-004",customer_id=c1.id,date=d(20),due_date=d(-10),status="unpaid",tax_pct=dec("14"),discount_pct=dec("0"),subtotal=dec("109649"),tax_amount=dec("15351"),discount_amt=dec("0"),   total=dec("125000"),paid_amount=dec("0"),    notes="الدفعة النهائية FG-500")
        db.add_all([inv1,inv2,inv3,inv4]); await db.flush()

        inv_lines=[
            InvoiceLine(tenant_id=TID, invoice_id=inv1.id,description="دفعة أولى — مجرشة FG-500",        qty=dec("1"),unit="pcs",unit_price=dec("109649"),total_price=dec("109649"),sort_order=1),
            InvoiceLine(tenant_id=TID, invoice_id=inv2.id,description="خلاط أعلاف MX-200 كامل",          qty=dec("1"),unit="pcs",unit_price=dec("115000"),total_price=dec("115000"),sort_order=1),
            InvoiceLine(tenant_id=TID, invoice_id=inv2.id,description="تركيب وشحن",                       qty=dec("1"),unit="pcs",unit_price=dec("5000"),  total_price=dec("5000"),  sort_order=2),
            InvoiceLine(tenant_id=TID, invoice_id=inv3.id,description="شاشات 3mm",                        qty=dec("4"),unit="pcs",unit_price=dec("1200"),  total_price=dec("4800"),  sort_order=1),
            InvoiceLine(tenant_id=TID, invoice_id=inv3.id,description="شاشات 5mm",                        qty=dec("2"),unit="pcs",unit_price=dec("1200"),  total_price=dec("2400"),  sort_order=2),
            InvoiceLine(tenant_id=TID, invoice_id=inv3.id,description="شحن وتوصيل",                       qty=dec("1"),unit="pcs",unit_price=dec("1800"),  total_price=dec("1800"),  sort_order=3),
            InvoiceLine(tenant_id=TID, invoice_id=inv4.id,description="الدفعة النهائية — FG-500",         qty=dec("1"),unit="pcs",unit_price=dec("109649"),total_price=dec("109649"),sort_order=1),
        ]
        db.add_all(inv_lines)
        db.add_all([
            CustomerPayment(tenant_id=TID, customer_id=c1.id,amount=dec("125000"),payment_type="bank_transfer",payment_date=d(87),reference="TRF-20240110",notes="تحويل بنكي دفعة أولى FG-500"),
            CustomerPayment(tenant_id=TID, customer_id=c4.id,amount=dec("9000"),  payment_type="cash",          payment_date=d(44),notes="نقدي — قطع غيار شاشات"),
        ])
        c1.balance=inv4.total; c2.balance=inv2.total
        await db.flush()
        print(f"✅ Invoices: 4  (lines: {len(inv_lines)})")

        # ── 12. WORK ORDERS ───────────────────────────────────────────
        wo1=WorkOrder(tenant_id=TID, code="WO-2024-001",equipment_id=fg.id,customer_order_id=orders_co[0].id,status="in_progress",planned_cost=dec("185000"),actual_cost=dec("0"),  start_date=d(80), end_date=d(-30), notes="FG-500 الدلتا للدواجن")
        wo2=WorkOrder(tenant_id=TID, code="WO-2024-002",equipment_id=mx.id,customer_order_id=orders_co[1].id,status="draft",      planned_cost=dec("95000"), actual_cost=dec("0"),  start_date=d(30), end_date=d(-60), notes="MX-200 مزرعة النيل")
        wo3=WorkOrder(tenant_id=TID, code="WO-2024-003",equipment_id=hp.id,customer_order_id=orders_co[3].id,status="draft",      planned_cost=dec("220000"),actual_cost=dec("0"),  start_date=d(10), end_date=d(-90), notes="HP-50T مصنع الغذاء")
        wo4=WorkOrder(tenant_id=TID, code="WO-2023-089",equipment_id=fg.id,                                  status="done",       planned_cost=dec("175000"),actual_cost=dec("168500"),start_date=d(200),end_date=d(110),notes="FG-500 سابق مكتمل")
        wo5=WorkOrder(tenant_id=TID, code="WO-2024-004",equipment_id=cn.id,customer_order_id=orders_co[4].id,status="draft",      planned_cost=dec("55000"), actual_cost=dec("0"),  start_date=d(5),  end_date=d(-45), notes="CNV-003 مؤسسة الرياض")
        db.add_all([wo1,wo2,wo3,wo4,wo5]); await db.flush()

        cost_lines=[
            CostLine(tenant_id=TID, work_order_id=wo1.id,cost_type="material",description="فولاذ ST-52 هيكل",    qty=dec("680"), unit_cost=dec("18.50"),  total_cost=dec("12580"),material_id=st52.id),
            CostLine(tenant_id=TID, work_order_id=wo1.id,cost_type="material",description="فولاذ ST-37 خارجي",   qty=dec("200"), unit_cost=dec("16.00"),  total_cost=dec("3200"), material_id=st37.id),
            CostLine(tenant_id=TID, work_order_id=wo1.id,cost_type="material",description="موتور 15KW",           qty=dec("1"),   unit_cost=dec("7200.00"),total_cost=dec("7200"), material_id=mot15.id),
            CostLine(tenant_id=TID, work_order_id=wo1.id,cost_type="material",description="انفرتر 15KW",          qty=dec("1"),   unit_cost=dec("5500.00"),total_cost=dec("5500"), material_id=vfd.id),
            CostLine(tenant_id=TID, work_order_id=wo1.id,cost_type="material",description="علبة تروس 1:20",       qty=dec("1"),   unit_cost=dec("3800.00"),total_cost=dec("3800"), material_id=gb20.id),
            CostLine(tenant_id=TID, work_order_id=wo1.id,cost_type="material",description="شاشات 3mm",            qty=dec("2"),   unit_cost=dec("1200.00"),total_cost=dec("2400"), material_id=scr3.id),
            CostLine(tenant_id=TID, work_order_id=wo1.id,cost_type="material",description="مواد استهلاكية",       qty=dec("1"),   unit_cost=dec("2800.00"),total_cost=dec("2800")),
            CostLine(tenant_id=TID, work_order_id=wo1.id,cost_type="labor",   description="لحام وتصنيع هيكل",    qty=dec("80"),  unit_cost=dec("250.00"), total_cost=dec("20000")),
            CostLine(tenant_id=TID, work_order_id=wo1.id,cost_type="labor",   description="تجميع وضبط وتشغيل",   qty=dec("40"),  unit_cost=dec("200.00"), total_cost=dec("8000")),
            CostLine(tenant_id=TID, work_order_id=wo1.id,cost_type="overhead",description="كهرباء وغاز وصيانة",  qty=dec("1"),   unit_cost=dec("6500.00"),total_cost=dec("6500")),
            CostLine(tenant_id=TID, work_order_id=wo4.id,cost_type="material",description="مواد إجمالي",          qty=dec("1"),   unit_cost=dec("98000.00"),total_cost=dec("98000")),
            CostLine(tenant_id=TID, work_order_id=wo4.id,cost_type="labor",   description="عمالة إجمالية",        qty=dec("1"),   unit_cost=dec("52000.00"),total_cost=dec("52000")),
            CostLine(tenant_id=TID, work_order_id=wo4.id,cost_type="overhead",description="مصاريف غير مباشرة",    qty=dec("1"),   unit_cost=dec("18500.00"),total_cost=dec("18500")),
        ]
        db.add_all(cost_lines)
        wo1.actual_cost=sum(c.total_cost for c in cost_lines if c.work_order_id==wo1.id)

        scraps=[
            ScrapRecord(tenant_id=TID, work_order_id=wo1.id,material_id=st52.id,qty=dec("18.5"),unit_cost=dec("18.50"),total_cost=dec("342.25"),reason="قطع خاطئ",           stage="قطع الهيكل", recorded_at=datetime(today.year,today.month,max(today.day-5,1))),
            ScrapRecord(tenant_id=TID, work_order_id=wo1.id,material_id=wr.id,  qty=dec("3.2"), unit_cost=dec("35.00"), total_cost=dec("112.00"),reason="إعادة لحام — عيب جودة",stage="لحام الهيكل",recorded_at=datetime(today.year,today.month,max(today.day-3,1))),
            ScrapRecord(tenant_id=TID, work_order_id=wo4.id,material_id=st37.id,qty=dec("12"),  unit_cost=dec("16.00"), total_cost=dec("192.00"),reason="خطأ في القص",          stage="تصنيع اللوح",recorded_at=datetime(today.year-1,12,10)),
        ]
        db.add_all(scraps)

        mrp=[
            MrpSuggestion(tenant_id=TID, material_id=st52.id, current_stock=dec("3780"),required_qty=dec("2000"),suggested_qty=dec("2500"),estimated_cost=dec("46250"),order_by_date=d(-10),expected_date=d(-3), status="pending"),
            MrpSuggestion(tenant_id=TID, material_id=mot75.id,current_stock=dec("7"),   required_qty=dec("3"),   suggested_qty=dec("3"),   estimated_cost=dec("12300"),order_by_date=d(-5), expected_date=d(25),  status="pending"),
            MrpSuggestion(tenant_id=TID, material_id=scr3.id, current_stock=dec("10"),  required_qty=dec("6"),   suggested_qty=dec("8"),   estimated_cost=dec("9600"), order_by_date=d(-2), expected_date=d(12),  status="approved"),
        ]
        db.add_all(mrp)

        sc=StockCount(tenant_id=TID, count_date=d(30),status="completed",notes="جرد دوري — نهاية الشهر")
        db.add(sc); await db.flush()
        sc_lines=[
            StockCountLine(tenant_id=TID, count_id=sc.id,material_id=st37.id, system_qty=dec("3000"),actual_qty=dec("2985"),difference=dec("-15")),
            StockCountLine(tenant_id=TID, count_id=sc.id,material_id=st52.id, system_qty=dec("4200"),actual_qty=dec("4200"),difference=dec("0")),
            StockCountLine(tenant_id=TID, count_id=sc.id,material_id=mot15.id,system_qty=dec("6"),   actual_qty=dec("6"),   difference=dec("0")),
            StockCountLine(tenant_id=TID, count_id=sc.id,material_id=wr.id,   system_qty=dec("200"), actual_qty=dec("196"), difference=dec("-4")),
        ]
        db.add_all(sc_lines); await db.flush()
        print(f"✅ Work Orders: 5  |  Cost Lines: {len(cost_lines)}  |  Scrap: {len(scraps)}  |  MRP: {len(mrp)}")

        # ── 13. CUSTOMER ASSETS ───────────────────────────────────────
        assets=[
            CustomerAsset(tenant_id=TID, customer_id=c1.id,equipment_id=fg.id,name="مجرشة FG-500 — الدلتا (خط 1)", serial_number="FG500-001-2023",model="FG-500 v2",delivery_date=d(200),warranty_end=d(-165),location="مصنع الدلتا — قسم العلف، المنصورة",          status="active"),
            CustomerAsset(tenant_id=TID, customer_id=c4.id,equipment_id=mx.id,name="خلاط MX-200 — الأندلس",         serial_number="MX200-007-2022",model="MX-200",    delivery_date=d(400),warranty_end=d(-35), location="مزرعة الأندلس — طريق الإسكندرية الصحراوي",    status="active"),
            CustomerAsset(tenant_id=TID, customer_id=c2.id,               name="ناقل أعلاف قديم 6م — النيل",       serial_number="CNV-003-2021",  model="CNV-003",   delivery_date=d(700),warranty_end=d(-340),location="مزرعة النيل — الجيزة",                          status="needs_maintenance"),
        ]
        db.add_all(assets); await db.flush()
        asset1,asset2,asset3=assets
        print(f"✅ Customer Assets: {len(assets)}")

        # ── 14. FIELD SERVICE ─────────────────────────────────────────
        fs_orders=[
            FieldServiceOrder(tenant_id=TID, code="FS-2024-001",customer_id=c1.id,asset_id=asset1.id,
                order_type="maintenance",title="صيانة دورية 6 أشهر — FG-500 الدلتا",
                description="تغيير شاشات التصفية وتشحيم البلي وفحص الموتور والانفرتر وضبط التوازن",
                priority="normal",status="completed",planned_date=d(30),completion_date=d(28),
                estimated_hours=dec("8"),actual_hours=dec("9.5"),
                labor_cost=dec("2850"),materials_cost=dec("3200"),total_cost=dec("6050")),
            FieldServiceOrder(tenant_id=TID, code="FS-2024-002",customer_id=c4.id,asset_id=asset2.id,
                order_type="repair",title="إصلاح طارئ — خلاط MX-200 الأندلس",
                description="إصلاح تسريب زيت من علبة التروس وتغيير الأوراق الجانبية",
                priority="urgent",status="completed",planned_date=d(15),completion_date=d(14),
                estimated_hours=dec("4"),actual_hours=dec("5"),
                labor_cost=dec("1500"),materials_cost=dec("2400"),total_cost=dec("3900")),
            FieldServiceOrder(tenant_id=TID, code="FS-2024-003",customer_id=c1.id,
                order_type="installation",title="تركيب وتشغيل FG-500 الجديدة — الدلتا خط 2",
                description="استلام المعدة وتركيبها وربطها بالكهرباء وتشغيل أولي وتدريب المشغلين",
                priority="high",status="in_progress",planned_date=d(-5),
                estimated_hours=dec("16"),actual_hours=dec("8")),
            FieldServiceOrder(tenant_id=TID, code="FS-2024-004",customer_id=c2.id,asset_id=asset3.id,
                order_type="inspection",title="فحص ناقل الأعلاف — مزرعة النيل",
                description="فحص شامل للحالة الميكانيكية وتقييم الحاجة للاستبدال",
                priority="normal",status="assigned",planned_date=d(-10),
                estimated_hours=dec("3")),
            FieldServiceOrder(tenant_id=TID, code="FS-2024-005",customer_id=c3.id,
                order_type="commissioning",title="تشغيل أولي HP-50T — مصنع الغذاء الحيواني",
                description="تشغيل أولي بعد التركيب واختبارات الحمل وتدريب المشغلين",
                priority="high",status="pending",planned_date=d(-30),
                estimated_hours=dec("12")),
        ]
        db.add_all(fs_orders); await db.flush()
        fs1,fs2,fs3,fs4,fs5=fs_orders

        db.add_all([
            ServiceAssignment(tenant_id=TID, service_order_id=fs1.id,worker_id=w5.id,role="فني رئيسي"),
            ServiceAssignment(tenant_id=TID, service_order_id=fs1.id,worker_id=w6.id,role="مساعد فني"),
            ServiceAssignment(tenant_id=TID, service_order_id=fs2.id,worker_id=w5.id,role="فني رئيسي"),
            ServiceAssignment(tenant_id=TID, service_order_id=fs3.id,worker_id=w5.id,role="مشرف تركيب"),
            ServiceAssignment(tenant_id=TID, service_order_id=fs3.id,worker_id=w6.id,role="فني ميكانيكا"),
            ServiceAssignment(tenant_id=TID, service_order_id=fs3.id,worker_id=w3.id,role="فني كهرباء"),
            ServiceAssignment(tenant_id=TID, service_order_id=fs4.id,worker_id=w8.id,role="فني صيانة"),
        ])
        db.add_all([
            ServiceVisit(tenant_id=TID, service_order_id=fs1.id,visit_date=d(28),arrival_time="08:00",departure_time="17:30",labor_hours=dec("9.5"),
                work_done="تغيير شاشات 3mm (2 قطعة) وتشحيم البلي وفحص الموتور والانفرتر وضبط التوازن",
                materials_used="2 شاشة 3mm، شحم SKF 400g، أطراف كهربائية",
                next_action="صيانة دورية بعد 6 أشهر",status="completed"),
            ServiceVisit(tenant_id=TID, service_order_id=fs2.id,visit_date=d(14),arrival_time="09:00",departure_time="14:30",labor_hours=dec("5"),
                work_done="إصلاح تسريب الزيت من حشية علبة التروس وتغيير لوحين جانبيين واختبار التشغيل",
                materials_used="حشية تروس، 2 لوح ST-37 3mm، زيت تروس 5 لتر",
                next_action="متابعة هاتفية بعد أسبوع",status="completed"),
            ServiceVisit(tenant_id=TID, service_order_id=fs3.id,visit_date=d(0),arrival_time="07:30",departure_time="15:00",labor_hours=dec("7.5"),
                work_done="تثبيت الهيكل وربط الأنابيب الهيدروليكية وبدء تمديد الأسلاك الكهربائية",
                materials_used="مسامير M16 (20 قطعة)، أنابيب هيدروليك 3م",
                next_action="إكمال الكهرباء وتشغيل أولي",status="completed"),
        ])
        await db.flush()
        print(f"✅ Field Service: {len(fs_orders)} أوامر")


        # ── 15. CHART OF ACCOUNTS ─────────────────────────────────────
        from sqlalchemy import select as _sel
        _coa_count = await db.scalar(_sel(ChartOfAccount).where(ChartOfAccount.tenant_id == TID).limit(1))
        if not _coa_count:
            _coa_defaults = [
                ("1000","الأصول",None,"asset","debit"),
                ("1100","النقدية والبنوك","1000","asset","debit"),
                ("1110","الصندوق","1100","asset","debit"),
                ("1120","البنك الرئيسي","1100","asset","debit"),
                ("1200","الذمم المدينة","1000","asset","debit"),
                ("1210","العملاء","1200","asset","debit"),
                ("1300","المخزون","1000","asset","debit"),
                ("1310","مخزون خام","1300","asset","debit"),
                ("1320","إنتاج تحت التشغيل (WIP)","1300","asset","debit"),
                ("1330","مخزون تام الصنع","1300","asset","debit"),
                ("2000","الخصوم",None,"liability","credit"),
                ("2100","الذمم الدائنة","2000","liability","credit"),
                ("2110","الموردون","2100","liability","credit"),
                ("2200","المستحقات","2000","liability","credit"),
                ("2210","الأجور المستحقة","2200","liability","credit"),
                ("3000","حقوق الملكية",None,"equity","credit"),
                ("3100","رأس المال","3000","equity","credit"),
                ("3200","الأرباح المحتجزة","3000","equity","credit"),
                ("4000","الإيرادات",None,"revenue","credit"),
                ("4100","إيرادات المبيعات","4000","revenue","credit"),
                ("4200","إيرادات المشاريع الخارجية","4000","revenue","credit"),
                ("4300","إيرادات الخدمة الميدانية","4000","revenue","credit"),
                ("5000","المصروفات",None,"expense","debit"),
                ("5100","تكلفة المواد المباشرة","5000","expense","debit"),
                ("5200","تكلفة العمالة المباشرة","5000","expense","debit"),
                ("5300","المصروفات الصناعية الغير مباشرة","5000","expense","debit"),
                ("5400","مصاريف بيعية وتسويقية","5000","expense","debit"),
                ("5500","مصاريف إدارية وعمومية","5000","expense","debit"),
                ("5600","مصاريف هالك وإتلاف","5000","expense","debit"),
            ]
            _code_to_id = {}
            for _code, _name, _parent_code, _atype, _nbal in _coa_defaults:
                _pid = _code_to_id.get(_parent_code)
                _acc = ChartOfAccount(tenant_id=TID, code=_code, name_ar=_name,
                    account_type=_atype, normal_balance=_nbal, parent_id=_pid)
                db.add(_acc); await db.flush()
                _code_to_id[_code] = _acc.id
            print(f"✅ دليل الحسابات: {len(_coa_defaults)} حساب")
        else:
            # Reload code_to_id for use below
            _code_to_id = {}
            _all_accs = (await db.execute(_sel(ChartOfAccount).where(ChartOfAccount.tenant_id==TID))).scalars().all()
            for _a in _all_accs:
                _code_to_id[_a.code] = _a.id

        # ── 16. SAMPLE JOURNAL ENTRIES ────────────────────────────────
        def _entry(desc, source, src_id, lines_data, entry_date=None):
            e = AccountingEntry(tenant_id=TID, entry_date=entry_date or today,
                description=desc, source=source, source_id=src_id, posted=True)
            db.add(e)
            return e, lines_data

        # قيد بيع: مدين العملاء، دائن الإيراد
        _e1 = AccountingEntry(tenant_id=TID, entry_date=d(30), description="فاتورة بيع INV-2024-001 — الدلتا",
            source="sales_invoice", source_id=1, posted=True)
        db.add(_e1); await db.flush()
        if _code_to_id.get("1210") and _code_to_id.get("4100"):
            db.add(AccountingEntryLine(entry_id=_e1.id, account_id=_code_to_id["1210"], side="debit",  amount=dec("180000")))
            db.add(AccountingEntryLine(entry_id=_e1.id, account_id=_code_to_id["4100"], side="credit", amount=dec("180000")))

        # قيد استلام دفعة
        _e2 = AccountingEntry(tenant_id=TID, entry_date=d(25), description="استلام دفعة مقدم — الدلتا",
            source="sales_payment", source_id=1, posted=True)
        db.add(_e2); await db.flush()
        if _code_to_id.get("1110") and _code_to_id.get("1210"):
            db.add(AccountingEntryLine(entry_id=_e2.id, account_id=_code_to_id["1110"], side="debit",  amount=dec("50000")))
            db.add(AccountingEntryLine(entry_id=_e2.id, account_id=_code_to_id["1210"], side="credit", amount=dec("50000")))

        # قيد شراء: مدين المخزون، دائن المورد
        _e3 = AccountingEntry(tenant_id=TID, entry_date=d(20), description="فاتورة شراء — حديد مقطعي",
            source="purchase_invoice", source_id=1, posted=True)
        db.add(_e3); await db.flush()
        if _code_to_id.get("1310") and _code_to_id.get("2110"):
            db.add(AccountingEntryLine(entry_id=_e3.id, account_id=_code_to_id["1310"], side="debit",  amount=dec("45000")))
            db.add(AccountingEntryLine(entry_id=_e3.id, account_id=_code_to_id["2110"], side="credit", amount=dec("45000")))

        await db.flush()
        print("✅ قيود محاسبية تجريبية: 3 قيود مرحّلة")

        # ── 17. PURCHASE ORDERS ──────────────────────────────────────
        po1 = PurchaseOrder(tenant_id=TID, code="PO-0001",
            supplier_id=1, supplier_name="مورد الحديد",
            order_date=d(45), expected_date=d(38),
            subtotal=dec("42000"), tax_pct=dec("14"), tax_amount=dec("5880"),
            discount_amt=dec("0"), total=dec("47880"), status="received",
            notes="طلب الكميات الشهرية من الحديد المقطعي والصاج")
        po2 = PurchaseOrder(tenant_id=TID, code="PO-0002",
            supplier_id=2, supplier_name="موردون الأنابيب",
            order_date=d(10), expected_date=d(3),
            subtotal=dec("18000"), tax_pct=dec("14"), tax_amount=dec("2520"),
            discount_amt=dec("0"), total=dec("20520"), status="approved",
            notes="أنابيب ستانلس ومواسير هيدروليك")
        po3 = PurchaseOrder(tenant_id=TID, code="PO-MRP-0001",
            order_date=d(2), expected_date=d(0)+__import__("datetime").timedelta(days=7),
            subtotal=dec("12500"), tax_pct=dec("14"), tax_amount=dec("1750"),
            discount_amt=dec("0"), total=dec("14250"), status="draft",
            notes="أُنشئ تلقائياً من اقتراح MRP #1")
        db.add_all([po1, po2, po3]); await db.flush()

        db.add_all([
            PurchaseOrderLine(order_id=po1.id, material_id=1, description="حديد مقطعي 50×50×5",
                ordered_qty=dec("500"), received_qty=dec("500"), unit="كجم", unit_cost=dec("42"), total_cost=dec("21000")),
            PurchaseOrderLine(order_id=po1.id, material_id=2, description="صاج 3mm",
                ordered_qty=dec("300"), received_qty=dec("300"), unit="كجم", unit_cost=dec("70"), total_cost=dec("21000")),
            PurchaseOrderLine(order_id=po2.id, material_id=3, description="أنابيب ستانلس 2 بوصة",
                ordered_qty=dec("100"), received_qty=dec("0"),   unit="متر", unit_cost=dec("120"), total_cost=dec("12000")),
            PurchaseOrderLine(order_id=po2.id, material_id=4, description="مواسير هيدروليك",
                ordered_qty=dec("50"),  received_qty=dec("0"),   unit="متر", unit_cost=dec("120"), total_cost=dec("6000")),
            PurchaseOrderLine(order_id=po3.id, description="مادة احتياطية — MRP",
                ordered_qty=dec("250"), received_qty=dec("0"),   unit="كجم", unit_cost=dec("50"),  total_cost=dec("12500")),
        ])
        await db.flush()
        print("✅ أوامر الشراء: 3 أوامر (مستلم/موافق/مسودة)")

        # ── 18. STOCK BATCHES (Lot Tracking) ─────────────────────────
        db.add_all([
            StockBatch(tenant_id=TID, material_id=1, lot_number="LOT-2024-001",
                supplier_id=1, purchase_invoice_id=1, received_date=d(60),
                qty_received=dec("500"), qty_remaining=dec("320"), unit_cost=dec("42"), status="active",
                notes="شحنة يناير من مورد الحديد الرئيسي"),
            StockBatch(tenant_id=TID, material_id=1, lot_number="LOT-2024-002",
                supplier_id=1, received_date=d(10),
                qty_received=dec("300"), qty_remaining=dec("300"), unit_cost=dec("43.5"), status="active",
                notes="شحنة فبراير — سعر جديد"),
            StockBatch(tenant_id=TID, material_id=2, lot_number="SHEET-2024-001",
                supplier_id=2, received_date=d(45),
                qty_received=dec("200"), qty_remaining=dec("80"), unit_cost=dec("70"), status="active",
                notes="صاج 3mm — دفعة الربع الأول"),
            StockBatch(tenant_id=TID, material_id=3, lot_number="TUBE-2024-001",
                received_date=d(30),
                qty_received=dec("100"), qty_remaining=dec("0"), unit_cost=dec("120"), status="consumed",
                notes="أنابيب ستانلس — مستهلكة بالكامل في الإنتاج"),
        ])
        await db.flush()
        print("✅ دُفعات المخزون: 4 دُفعات (lot tracking)")

        # ── 19. PRODUCTION SCHEDULES ──────────────────────────────────
        # Get first few work orders
        from sqlalchemy import select as _selwo
        _wos = (await db.execute(_selwo(WorkOrder).limit(4))).scalars().all()
        _sched_added = 0
        for _i, _wo in enumerate(_wos[:3]):
            _start = d(-(5 * _i))
            _end   = _start + __import__("datetime").timedelta(days=14)
            _status = ["completed", "in_progress", "planned"][_i]
            _sched = ProductionSchedule(
                tenant_id=TID, work_order_id=_wo.id,
                scheduled_start=_start, scheduled_end=_end,
                actual_start=_start if _status in ("completed","in_progress") else None,
                actual_end=_end if _status == "completed" else None,
                priority=_i+1, status=_status,
                notes=f"جدول أمر الإنتاج {_wo.code}",
            )
            db.add(_sched)
            _sched_added += 1
        await db.flush()
        print(f"✅ جداول الإنتاج: {_sched_added} جدول")

        # ── 20. WORK ORDER STAGES ─────────────────────────────────────
        _stage_defs = [
            (1,"قطع وتشكيل"), (2,"لحام"), (3,"تجميع"),
            (4,"طلاء وتشطيب"), (5,"اختبار وضبط الجودة"), (6,"تغليف وتسليم"),
        ]
        _stages_added = 0
        if _wos:
            _wo_main = _wos[0]
            for _order, _sname in _stage_defs:
                _st = "done" if _order <= 4 else ("in_progress" if _order == 5 else "pending")
                _sobj = WorkOrderStage(
                    tenant_id=TID, work_order_id=_wo_main.id,
                    stage_name=_sname, stage_order=_order, status=_st,
                    planned_hours=dec(str(_order * 2)),
                    actual_hours=dec(str(_order * 2 + 0.5)) if _st == "done" else None,
                    started_at=datetime.now() - __import__("datetime").timedelta(days=14-_order*2) if _st in ("done","in_progress") else None,
                    completed_at=datetime.now() - __import__("datetime").timedelta(days=14-_order*2-1) if _st=="done" else None,
                )
                db.add(_sobj)
                _stages_added += 1
        await db.flush()
        print(f"✅ مراحل الإنتاج: {_stages_added} مرحلة للأمر الأول")

        # ── 21. QUALITY INSPECTIONS ───────────────────────────────────
        _qc_list = []
        if _wos:
            _qc_list = [
                QualityInspection(tenant_id=TID, work_order_id=_wos[0].id,
                    inspection_date=d(5), inspection_type="in_process",
                    qty_inspected=dec("50"), qty_passed=dec("48"), qty_failed=dec("1"), qty_rework=dec("1"),
                    result="conditional",
                    failure_reason="ضعف في لحام الزاوية الأمامية",
                    corrective_action="إعادة لحام وفحص مرة ثانية",
                    notes="فحص أثناء مرحلة اللحام"),
                QualityInspection(tenant_id=TID, work_order_id=_wos[0].id,
                    inspection_date=d(2), inspection_type="final",
                    qty_inspected=dec("1"), qty_passed=dec("1"), qty_failed=dec("0"), qty_rework=dec("0"),
                    result="pass",
                    notes="الفحص النهائي — اجتاز جميع المعايير"),
            ]
            if len(_wos) > 1:
                _qc_list.append(
                    QualityInspection(tenant_id=TID, work_order_id=_wos[1].id,
                        inspection_date=d(1), inspection_type="in_process",
                        qty_inspected=dec("30"), qty_passed=dec("0"), qty_failed=dec("30"), qty_rework=dec("0"),
                        result="fail",
                        failure_reason="تشقق في الهيكل — خامة غير مطابقة للمواصفات",
                        corrective_action="إعادة تصنيع بمواد بديلة — فتح أمر إعادة عمل WO-RW-0001")
                )
        db.add_all(_qc_list); await db.flush()
        print(f"✅ فحوصات الجودة: {len(_qc_list)} فحص (pass/conditional/fail)")


        # ── 22. TREASURY ─────────────────────────────────────────────
        tr_main = Treasury(tenant_id=TID, name="الخزنة الرئيسية", currency="EGP", balance=dec("124500"), is_active=True)
        tr_petty = Treasury(tenant_id=TID, name="خزنة المصاريف النثرية", currency="EGP", balance=dec("8200"), is_active=True)
        db.add_all([tr_main, tr_petty]); await db.flush()
        cat_recv = TreasuryCategory(tenant_id=TID, name="مقبوضات عملاء", category_type="income", treasury_id=tr_main.id)
        cat_pay  = TreasuryCategory(tenant_id=TID, name="مدفوعات موردين", category_type="expense", treasury_id=tr_main.id)
        cat_misc = TreasuryCategory(tenant_id=TID, name="مصاريف متنوعة", category_type="expense", treasury_id=tr_petty.id)
        db.add_all([cat_recv, cat_pay, cat_misc]); await db.flush()
        db.add_all([
            TreasuryTransaction(tenant_id=TID, treasury_id=tr_main.id, category_id=cat_recv.id,
                date=d(88), direction="in", amount=dec("125000"), party_name="شركة الدلتا للدواجن",
                reference="INV-2024-001", description="سداد فاتورة FG-500 أولى"),
            TreasuryTransaction(tenant_id=TID, treasury_id=tr_main.id, category_id=cat_pay.id,
                date=d(70), direction="out", amount=dec("45000"), party_name="شركة الحديد والصلب",
                reference="PO-001", description="دفعة مستلزمات إنتاج"),
            TreasuryTransaction(tenant_id=TID, treasury_id=tr_main.id, category_id=cat_recv.id,
                date=d(45), direction="in", amount=dec("9000"), party_name="شركة الأندلس للزراعة",
                reference="INV-2024-003", description="سداد فاتورة شاشات"),
            TreasuryTransaction(tenant_id=TID, treasury_id=tr_petty.id, category_id=cat_misc.id,
                date=d(10), direction="out", amount=dec("1300"), party_name="مستلزمات مكتبية",
                description="مستلزمات ورق وطباعة"),
        ])
        await db.flush()
        print(f"✅ الخزنة: خزينتان + 3 فئات + 4 معاملات")

        # ── 23. SUPPLY CONTRACTS ───────────────────────────────────────
        sc1 = SupplyContract(
            tenant_id=TID, code="SC-2024-001", customer_id=c1.id, invoice_id=None,
            date=d(120), start_date=d(120), end_date=d(-240),
            project_name="مجرشة أعلاف FG-500 كاملة", project_location="مصنع الدلتا — المنصورة",
            contract_value=dec("250000"), advance_pct=dec("30"), advance_amount=dec("75000"),
            payment_terms="30% مقدم — 40% عند التسليم — 30% بعد التشغيل",
            warranty_months=12, status="active",
            scope_summary="توريد وتركيب وتشغيل مجرشة أعلاف مزدوجة الطور FG-500 مع التدريب",
            notes="عقد موقع ومصدق"
        )
        sc2 = SupplyContract(
            tenant_id=TID, code="SC-2024-002", customer_id=c2.id, invoice_id=None,
            date=d(60), start_date=d(60), end_date=d(-180),
            project_name="خلاط أعلاف MX-200", project_location="مزرعة النيل — الجيزة",
            contract_value=dec("130800"), advance_pct=dec("25"), advance_amount=dec("32700"),
            payment_terms="25% مقدم — 75% عند الاستلام",
            warranty_months=12, status="active",
            scope_summary="توريد وتركيب خلاط أعلاف أفقي MX-200 مع ناقل إدخال",
        )
        db.add_all([sc1, sc2]); await db.flush()
        db.add_all([
            SupplyContractLine(tenant_id=TID, contract_id=sc1.id, description="مجرشة FG-500 مزدوجة", qty=dec("1"), unit="قطعة", unit_price=dec("175000"), total_price=dec("175000"), sort_order=1),
            SupplyContractLine(tenant_id=TID, contract_id=sc1.id, description="تركيب وتشغيل", qty=dec("1"), unit="خدمة", unit_price=dec("25000"), total_price=dec("25000"), sort_order=2),
            SupplyContractLine(tenant_id=TID, contract_id=sc1.id, description="تدريب فريق التشغيل", qty=dec("3"), unit="يوم", unit_price=dec("5000"), total_price=dec("15000"), sort_order=3),
            SupplyContractLine(tenant_id=TID, contract_id=sc2.id, description="خلاط MX-200 أفقي", qty=dec("1"), unit="قطعة", unit_price=dec("115000"), total_price=dec("115000"), sort_order=1),
            SupplyContractLine(tenant_id=TID, contract_id=sc2.id, description="ناقل إدخال 3م", qty=dec("1"), unit="قطعة", unit_price=dec("15800"), total_price=dec("15800"), sort_order=2),
        ])
        await db.flush()
        print(f"✅ عقود التوريد: 2 عقود + 5 بنود")

        # ── 24. CREDIT NOTES ──────────────────────────────────────────
        cn1 = CreditNote(
            tenant_id=TID, code="CN-2024-001", invoice_id=inv3.id, customer_id=c4.id,
            date=d(40), reason="بضاعة مرتجعة — عيب في التصنيع",
            return_type="partial",
            subtotal=dec("2400"), tax_pct=dec("0"), tax_amount=dec("0"),
            discount_amt=dec("0"), total=dec("2400"),
            status="issued", issued_at=datetime.now()-__import__("datetime").timedelta(days=38),
            notes="إرجاع شاشتين 5mm بسبب تشققات"
        )
        cn2 = CreditNote(
            tenant_id=TID, code="CN-2024-002", invoice_id=inv2.id, customer_id=c2.id,
            date=d(5), reason="خصم تفاوضي على تأخر التسليم",
            return_type="partial",
            subtotal=dec("5000"), tax_pct=dec("14"), tax_amount=dec("700"),
            discount_amt=dec("0"), total=dec("5700"),
            status="draft",
            notes="في انتظار موافقة المدير"
        )
        db.add_all([cn1, cn2]); await db.flush()
        # Update customer balance for issued CN
        c4.balance = (c4.balance or dec("0")) - cn1.total
        db.add_all([
            CreditNoteLine(tenant_id=TID, credit_note_id=cn1.id, description="شاشات 5mm — مرتجع", qty=dec("2"), unit="قطعة", unit_price=dec("1200"), total_price=dec("2400"), sort_order=1),
            CreditNoteLine(tenant_id=TID, credit_note_id=cn2.id, description="خصم تأخر تسليم", qty=dec("1"), unit="خدمة", unit_price=dec("5000"), total_price=dec("5000"), sort_order=1),
        ])
        await db.flush()
        print(f"✅ إشعارات دائنة: 2 إشعار (1 صادر + 1 مسودة)")

        # ── 25. WORK COMPLETION CERTS ─────────────────────────────────
        wcc1 = WorkCompletionCert(
            tenant_id=TID, code="WCC-2024-001", customer_id=c1.id,
            contract_id=sc1.id, invoice_id=inv1.id,
            date=d(60), project_name="مجرشة أعلاف FG-500",
            project_location="مصنع الدلتا للدواجن — المنصورة، الدقهلية",
            start_date=d(120), end_date=d(60),
            contract_value=dec("250000"), warranty_months=12,
            status="issued", issued_at=datetime.now()-__import__("datetime").timedelta(days=58),
            scope_summary="تم توريد وتركيب وتشغيل مجرشة أعلاف FG-500 مزدوجة الطور 45kW بالكامل وفق المواصفات التعاقدية",
            notes="اجتازت جميع اختبارات القبول والتشغيل"
        )
        wcc2 = WorkCompletionCert(
            tenant_id=TID, code="WCC-2024-002", customer_id=c2.id,
            contract_id=sc2.id, invoice_id=inv2.id,
            date=d(10), project_name="خلاط أعلاف MX-200",
            project_location="مزرعة النيل للأعلاف — الجيزة",
            start_date=d(60), end_date=d(10),
            contract_value=dec("130800"), warranty_months=12,
            status="draft",
            scope_summary="توريد وتركيب خلاط أعلاف MX-200 مع ناقل إدخال — في انتظار إصدار شهادة الإتمام",
        )
        db.add_all([wcc1, wcc2]); await db.flush()
        db.add_all([
            WorkCompletionItem(tenant_id=TID, cert_id=wcc1.id, description="مجرشة FG-500 مزدوجة", qty=dec("1"), unit="قطعة", unit_price=dec("175000"), total_price=dec("175000"), sort_order=1),
            WorkCompletionItem(tenant_id=TID, cert_id=wcc1.id, description="أعمال التركيب والتشغيل", qty=dec("1"), unit="خدمة", unit_price=dec("25000"), total_price=dec("25000"), sort_order=2),
            WorkCompletionItem(tenant_id=TID, cert_id=wcc1.id, description="التدريب (3 أيام)", qty=dec("3"), unit="يوم", unit_price=dec("5000"), total_price=dec("15000"), sort_order=3),
            WorkCompletionItem(tenant_id=TID, cert_id=wcc2.id, description="خلاط MX-200", qty=dec("1"), unit="قطعة", unit_price=dec("115000"), total_price=dec("115000"), sort_order=1),
            WorkCompletionItem(tenant_id=TID, cert_id=wcc2.id, description="ناقل إدخال 3م", qty=dec("1"), unit="قطعة", unit_price=dec("15800"), total_price=dec("15800"), sort_order=2),
        ])
        await db.flush()
        print(f"✅ شهادات إتمام: 2 (1 صادرة + 1 مسودة)")

        # ── 26. WARRANTY CERTIFICATES ─────────────────────────────────
        from datetime import date as _date
        db.add_all([
            WarrantyCertificate(
                tenant_id=TID, code="WR-2024-001",
                customer_id=c1.id, invoice_id=inv1.id, wcc_id=wcc1.id,
                date=d(58), product_name="مجرشة أعلاف FG-500 مزدوجة",
                model_no="FG-500-v2", serial_no="FG500-001-2024",
                warranty_months=12,
                warranty_start=d(60),
                warranty_end=d(60) + __import__("datetime").timedelta(days=365),
                covered_items="عيوب التصنيع — المحرك — التروس — لوحة التحكم — مواد الهيكل",
                exclusions="الإساءة في الاستخدام — الحوادث — التعديلات غير المرخصة — التآكل الطبيعي",
                service_contact="خدمة العملاء: 01001234567 — support@warsha.eg",
                status="issued",
                issued_at=datetime.now()-__import__("datetime").timedelta(days=56),
            ),
            WarrantyCertificate(
                tenant_id=TID, code="WR-2024-002",
                customer_id=c2.id, invoice_id=inv2.id, wcc_id=None,
                date=d(8), product_name="خلاط أعلاف MX-200 أفقي",
                model_no="MX-200", serial_no="MX200-007-2024",
                warranty_months=12,
                warranty_start=d(10),
                warranty_end=d(10) + __import__("datetime").timedelta(days=365),
                covered_items="عيوب التصنيع — الذراع الدوارة — المحرك — جسم الخلاط",
                exclusions="الحمل الزائد — المواد الخاطئة — عدم الصيانة الدورية",
                service_contact="خدمة العملاء: 01001234567",
                status="draft",
            ),
            WarrantyCertificate(
                tenant_id=TID, code="WR-2023-005",
                customer_id=c4.id, invoice_id=None, wcc_id=None,
                date=d(400), product_name="ناقل أعلاف CNV-003",
                model_no="CNV-003", serial_no="CNV-003-2022",
                warranty_months=12,
                warranty_start=d(400),
                warranty_end=d(400) + __import__("datetime").timedelta(days=365),
                covered_items="عيوب التصنيع",
                exclusions="التآكل الطبيعي — عدم الصيانة",
                service_contact="خدمة العملاء: 01001234567",
                status="issued",
                issued_at=datetime.now()-__import__("datetime").timedelta(days=398),
            ),
        ])
        await db.flush()
        print(f"✅ شهادات ضمان: 3 (1 صادرة ساري + 1 مسودة + 1 منتهي)")

        await db.commit()

        print()
        print("=" * 62)
        print("  ✅  SEED COMPLETE — WarshERP ورشة معدات الأعلاف")
        print("=" * 62)
        print(f"  👥  المستخدمون      : {len(users)}")
        print(f"  📦  مواد            : {len(mats)} صنف")
        print(f"  🚚  موردون          : {len(suppliers)}")
        print(f"  ⚙️   معدات           : 4  (FG-500 / MX-200 / HP-50T / CNV-003)")
        print(f"  📐  سطور BOM        : {len(bom)}")
        print(f"  👷  عاملون          : {len(workers)}")
        print(f"  🏢  عملاء           : {len(customers)}")
        print(f"  🏭  أوامر إنتاج     : 5")
        print(f"  🛠️   أوامر ميدانية  : {len(fs_orders)}")
        print(f"  📊  دليل الحسابات  : 29 حساب")
        print(f"  📋  أوامر الشراء    : 3 أوامر")
        print(f"  🔍  فحوصات الجودة  : {len(_qc_list)} فحوصات")
        print(f"  📦  دُفعات مخزون   : 4 دُفعات")
        print(f"  💰  خزنة            : 2 خزائن + 4 معاملات")
        print(f"  📄  عقود توريد     : 2 عقود")
        print(f"  📋  إشعارات دائنة  : 2 إشعارات")
        print(f"  🏆  شهادات إتمام   : 2 شهادات")
        print(f"  🛡️   شهادات ضمان   : 3 شهادات")
        print("=" * 62)
        print("  🔑  بيانات الدخول:")
        print("      admin        / admin123")
        print("      accountant   / accountant123")
        print("      production   / production123")
        print("      viewer       / viewer123")
        print("      storekeeper  / store123")
        print("=" * 62)


if __name__ == "__main__":
    async def _main():
        await wipe_all()
        await seed()
    asyncio.run(_main())
