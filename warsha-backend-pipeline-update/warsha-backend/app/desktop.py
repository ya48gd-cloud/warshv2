import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select

from app.api.auth import hash_password
from app.core.database import AsyncSessionLocal, engine, current_tenant_id
from app.models.models import Base, User, Tenant, MaterialCategory, Treasury, TreasuryCategory


# الأصناف الجاهزة اللي بتتعمل تلقائياً لورشة تصنيع المعدات
DEFAULT_CATEGORIES = [
    ("حديد ومعادن",        "Steel & Metals"),
    ("ألواح وصاج",          "Plates & Sheets"),
    ("تروس وناقلات حركة",   "Gears & Drives"),
    ("براغي وصواميل",       "Fasteners"),
    ("رمان بلي ومحامل",     "Bearings"),
    ("موتورات",             "Motors"),
    ("كهرباء وتحكم",        "Electrical & Control"),
    ("سيور ونقل حركة",      "Belts & Transmission"),
    ("هيدروليك ونيوماتيك",  "Hydraulics & Pneumatics"),
    ("دهانات ومواد كيماوية", "Paints & Chemicals"),
    ("قطع غيار",            "Spare Parts"),
    ("مستهلكات",            "Consumables"),
]


# فئات حركات الخزنة الجاهزة
DEFAULT_TREASURY_CATEGORIES = [
    ("تحصيل من عملاء", "in"),
    ("دفع لموردين",    "out"),
    ("مرتبات",         "out"),
    ("إيجار",          "out"),
    ("كهرباء ومياه",   "out"),
    ("خامات",          "out"),
    ("صيانة",          "out"),
    ("مصاريف نقل",     "out"),
    ("مصاريف متنوعة",  "out"),
    ("إيرادات أخرى",   "in"),
]


async def init_db(seed_demo: bool = False) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # ── Column migrations for existing databases ──────────────────
        _is_sqlite = str(conn.engine.url).startswith("sqlite")
        if _is_sqlite:
            _migrations = [
                ("suppliers",         "email",          "VARCHAR(200)"),
                ("suppliers",         "contact_person", "VARCHAR(200)"),
                ("accounting_entries","period_year",     "INTEGER"),
                ("accounting_entries","period_month",    "INTEGER"),
                # customer_locations FK columns added to existing tables
                ("customer_orders",   "location_id",    "INTEGER"),
                ("customer_payments", "location_id",    "INTEGER"),
                ("invoices",          "location_id",    "INTEGER"),
                ("ext_projects",      "location_id",    "INTEGER"),
                # external-projects link on attendance
                ("attendance",        "ext_project_id", "INTEGER"),
                # supply_contracts delivery confirmation fields
                ("supply_contracts",  "delivery_confirmed", "INTEGER DEFAULT 0"),
                ("supply_contracts",  "delivery_date",      "DATE"),
                ("supply_contracts",  "received_by",        "VARCHAR(200)"),
                ("supply_contracts",  "receiver_title",     "VARCHAR(200)"),
                ("supply_contracts",  "delivery_notes",     "TEXT"),
                ("supply_contracts",  "vehicle_plate",      "VARCHAR(50)"),
                ("supply_contracts",  "vehicle_type",       "VARCHAR(100)"),
                ("supply_contracts",  "driver_name",        "VARCHAR(200)"),
                ("supply_contracts",  "driver_license",     "VARCHAR(100)"),
                ("supply_contracts",  "driver_phone",       "VARCHAR(50)"),
                # supply_contract_lines: ربط بالمواد
                ("supply_contract_lines", "material_id",    "INTEGER"),
                # workshop_profile: اللوجو كـ base64 data:URI في الداتابيز
                ("workshop_profile",      "logo_data",      "TEXT"),
                # delivery_orders & delivery_order_lines — أوامر التوريد
                ("delivery_orders",       "reference",      "VARCHAR(200)"),
                ("delivery_orders",       "delivery_date",  "DATE"),
                ("delivery_orders",       "delivery_address","TEXT"),
                ("delivery_orders",       "received_by",    "VARCHAR(200)"),
                ("delivery_orders",       "receiver_title", "VARCHAR(100)"),
                ("delivery_orders",       "receiver_phone", "VARCHAR(50)"),
                ("delivery_orders",       "delivery_notes", "TEXT"),
                ("delivery_orders",       "vehicle_plate",  "VARCHAR(50)"),
                ("delivery_orders",       "vehicle_type",   "VARCHAR(100)"),
                ("delivery_orders",       "driver_name",    "VARCHAR(200)"),
                ("delivery_orders",       "driver_license", "VARCHAR(100)"),
                ("delivery_orders",       "driver_phone",   "VARCHAR(50)"),
                # ── forms_analysis_report improvements ──────────────
                # Customers: شروط الدفع
                ("customers",             "payment_terms",  "VARCHAR(100)"),
                # Suppliers: بيانات بنكية + نوع المورد
                ("suppliers",             "bank_account",   "VARCHAR(200)"),
                ("suppliers",             "iban",           "VARCHAR(50)"),
                ("suppliers",             "supplier_type",  "VARCHAR(30)"),
                # Equipment: رقم مسلسل + حالة تشغيلية + بيانات شراء
                ("equipment",             "serial_number",  "VARCHAR(100)"),
                ("equipment",             "equip_status",   "VARCHAR(30) DEFAULT 'active'"),
                ("equipment",             "purchase_date",  "DATE"),
                ("equipment",             "purchase_value", "NUMERIC(14,2)"),
                # Workers: بيانات العقد والتأمين
                ("workers",               "contract_type",     "VARCHAR(30)"),
                ("workers",               "insurance_number",  "VARCHAR(50)"),
                ("workers",               "contract_end_date", "DATE"),
                # WorkOrders: تاريخ انتهاء مخطط + أولوية
                ("work_orders",           "planned_end_date",  "DATE"),
                ("work_orders",           "priority",          "VARCHAR(20) DEFAULT 'normal'"),
                # PurchaseInvoices: كود داخلي تلقائي
                ("purchase_invoices",     "internal_code",     "VARCHAR(50)"),
                # TreasuryTransactions: رقم إيصال تلقائي
                ("treasury_transactions", "receipt_no",        "VARCHAR(50)"),
                # CustomerOrders: ربط ببنود متعددة (الجدول الجديد تُنشئه create_all)
                # customer_order_lines يُنشأ تلقائياً
                # CustomerOrders: تحويل من عرض سعر
                ("customer_orders",      "quotation_id",      "INTEGER"),
                # Invoices: تحويل من طلب عميل / أمر إنتاج
                ("invoices",              "customer_order_id", "INTEGER"),
                ("invoices",              "work_order_id",     "INTEGER"),
                # WarrantyCertificate: تاريخ الإصدار
                ("warranty_certificates", "issued_at",         "DATETIME"),
                # WorkOrder: كمية الإنتاج
                ("work_orders",           "quantity",          "NUMERIC(12,4) DEFAULT 1"),
                ("work_orders",           "quantity_unit",     "VARCHAR(20) DEFAULT 'قطعة'"),
                # PurchaseOrderLine: tenant_id
                ("purchase_order_lines",  "tenant_id",         "INTEGER"),
                # Equipment: وحدة الحساب البارامتري + الكمية الأساسية
                ("equipment",             "calc_unit",         "VARCHAR(20) DEFAULT 'piece'"),
                ("equipment",             "base_qty",          "NUMERIC(12,4) DEFAULT 1"),
                # مواقع العميل: ربط عروض الأسعار وأوامر الخدمة الميدانية بموقع
                ("quotations",            "location_id",       "INTEGER"),
                ("field_service_orders",  "location_id",       "INTEGER"),
                # ScrapRecord: تتبع الكمية المباعة من الهالك
                ("scrap_records",         "qty_sold",          "NUMERIC(14,3) DEFAULT 0"),
                # محطة 2: عكس حركة المخزون بدل حذفها فعليًا (audit trail)
                ("stock_movements",       "state",              "VARCHAR(20) DEFAULT 'posted'"),
                ("stock_movements",       "reversed_movement_id","INTEGER"),
                # محطة 3: تتبع مصدر الحركة + رقم تسلسلي موحّد (الخدمة المركزية للمخزون)
                ("stock_movements",       "movement_no",        "INTEGER"),
                ("stock_movements",       "source_doc_type",    "VARCHAR(50)"),
                ("stock_movements",       "source_doc_id",      "INTEGER"),
                # محطة 9: سياسات الصنف
                ("materials",             "stock_item_type",    "VARCHAR(30) DEFAULT 'raw_material'"),
                ("materials",             "tracking",           "VARCHAR(10) DEFAULT 'none'"),
                ("materials",             "valuation_method",   "VARCHAR(20) DEFAULT 'moving_average'"),
                ("materials",             "removal_strategy",   "VARCHAR(10) DEFAULT 'fifo'"),
                # محطة 11: ترقية BinLocation نحو StockLocation
                ("bin_locations",         "warehouse_id",       "INTEGER"),
                ("bin_locations",         "parent_id",          "INTEGER"),
                ("bin_locations",         "location_type",      "VARCHAR(20) DEFAULT 'physical'"),
                # محطة 12: مصدر ووجهة الحركة على مستوى الموقع
                ("stock_movements",       "source_location_id",      "INTEGER"),
                ("stock_movements",       "destination_location_id", "INTEGER"),
            ]
            for table, col, col_type in _migrations:
                try:
                    await conn.execute(
                        __import__("sqlalchemy").text(
                            f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"
                        )
                    )
                except Exception:
                    pass  # column already exists

    if seed_demo:
        from seed import seed, wipe_all
        await wipe_all()
        await seed()
        return

    async with AsyncSessionLocal() as db:
        # ── 1) تأكد من وجود tenant افتراضي ──
        result = await db.execute(select(Tenant).where(Tenant.slug == "default"))
        tenant = result.scalar_one_or_none()
        if not tenant:
            tenant = Tenant(name="الورشة", slug="default", plan="pro", is_active=True)
            db.add(tenant)
            await db.flush()

        # نحط الـ tenant في الـ context عشان الحقن التلقائي يشتغل
        current_tenant_id.set(tenant.id)

        # ── 2) تأكد من وجود admin user ──
        admin_q = await db.execute(
            select(User).where(User.username == "admin", User.tenant_id == tenant.id)
        )
        if not admin_q.scalar_one_or_none():
            db.add(User(
                tenant_id=tenant.id,
                username="admin",
                password_hash=hash_password("admin123"),
                full_name="مدير النظام",
                role="admin",
                is_active=True,
            ))

        # ── 3) أضف الأصناف الجاهزة (بس لو مش موجودة) ──
        existing_cats = await db.execute(
            select(MaterialCategory.name_ar).where(MaterialCategory.tenant_id == tenant.id)
        )
        existing_names = {row[0] for row in existing_cats.all()}
        added = 0
        for name_ar, name_en in DEFAULT_CATEGORIES:
            if name_ar not in existing_names:
                db.add(MaterialCategory(tenant_id=tenant.id, name_ar=name_ar, name_en=name_en))
                added += 1

        # ── 4) أضف خزنة افتراضية + فئات الحركات (بس لو مفيش خزن) ──
        treas_count = await db.execute(
            select(Treasury.id).where(Treasury.tenant_id == tenant.id).limit(1)
        )
        has_treasury = treas_count.first() is not None
        if not has_treasury:
            db.add(Treasury(tenant_id=tenant.id, name="الخزنة الرئيسية", kind="cash",
                            opening_balance=0, balance=0, is_active=True))

        existing_tcats = await db.execute(
            select(TreasuryCategory.name).where(TreasuryCategory.tenant_id == tenant.id)
        )
        existing_tnames = {row[0] for row in existing_tcats.all()}
        for name, flow in DEFAULT_TREASURY_CATEGORIES:
            if name not in existing_tnames:
                db.add(TreasuryCategory(tenant_id=tenant.id, name=name, flow=flow))

        # ── 5) أضف المخازن الافتراضية الـ8 (محطة 10 — بس لو ناقصة) ──
        from app.api.warehouses import seed_default_warehouses
        await seed_default_warehouses(db, tenant.id)

        await db.commit()
        print(f"تم تجهيز قاعدة البيانات — admin جاهز، وتمت إضافة {added} صنف جديد")


def main() -> None:
    parser = argparse.ArgumentParser(description="Heavy ERP desktop database setup")
    parser.add_argument("--seed-demo", action="store_true", help="Reset and load demo data")
    args = parser.parse_args()

    db_url = str(engine.url)
    if db_url.startswith("sqlite"):
        db_path = db_url.rsplit("///", 1)[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    asyncio.run(init_db(seed_demo=args.seed_demo))


if __name__ == "__main__":
    main()
