"""
سكريبت تصالح read-only — يُستخدم قبل/بعد كل Phase من خطة إصلاح الفجوات المالية.
لا يكتب أي شيء في قاعدة البيانات. يطبع:
  1) رصيد كل خزنة (Treasury.balance) مقابل صافي حركاتها (treasury_transactions، باستثناء transfer)
  2) رصيد حساب 1110 (الصندوق) و1120 (البنك) من دفتر الحسابات (accounting_entry_lines)
  3) الفرق بين (1) و(2) — المقياس الأساسي لمدى تقدّم إصلاح الفجوة
  4) قائمة كل الحسابات الشاذة (الرصيد المُعدَّل العلامة سالب وهو من المفروض دائن أو موجب وهو من المفروض مدين)

تشغيل:
  cd backend && DATABASE_URL="sqlite+aiosqlite:///C:/WarshERP-runtime/data/heavy_erp.db" \
    "/c/WarshERP-runtime/.venv/Scripts/python.exe" scripts/reconcile_treasury_vs_gl.py
"""
import asyncio
import io
import os
import sys
from decimal import Decimal

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

DB_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./warsha_test.db")


async def main():
    from app.models.models import (
        Treasury, TreasuryTransaction, ChartOfAccount, AccountingEntryLine, AccountingEntry,
    )

    engine = create_async_engine(DB_URL)
    async with AsyncSession(engine) as db:
        print("=" * 70)
        print("1) أرصدة الخزائن مقابل صافي حركاتها (treasury_transactions)")
        print("=" * 70)
        treasuries = (await db.execute(select(Treasury))).scalars().all()
        for t in treasuries:
            net = (await db.execute(
                select(
                    func.coalesce(func.sum(
                        TreasuryTransaction.amount * (1 if TreasuryTransaction.direction == "in" else -1)
                    ), 0)
                ).where(
                    TreasuryTransaction.treasury_id == t.id,
                    TreasuryTransaction.source != "transfer",
                )
            )).scalar() or Decimal(0)
            # نحسب transfer بشكل صريح لأن SQLAlchemy مش بيقدر يعمل CASE بهذا الشكل المباشر مباشرة
            in_sum = (await db.execute(
                select(func.coalesce(func.sum(TreasuryTransaction.amount), 0)).where(
                    TreasuryTransaction.treasury_id == t.id, TreasuryTransaction.direction == "in",
                )
            )).scalar() or Decimal(0)
            out_sum = (await db.execute(
                select(func.coalesce(func.sum(TreasuryTransaction.amount), 0)).where(
                    TreasuryTransaction.treasury_id == t.id, TreasuryTransaction.direction == "out",
                )
            )).scalar() or Decimal(0)
            computed = Decimal(str(t.opening_balance or 0)) + Decimal(str(in_sum)) - Decimal(str(out_sum))
            diff = Decimal(str(t.balance)) - computed
            flag = "  ⚠️ فرق" if abs(diff) > Decimal("0.01") else "  ✓"
            print(f"  [{t.id}] {t.name} ({t.kind}): stored_balance={t.balance} | computed_from_txns={computed} | diff={diff}{flag}")

        print()
        print("=" * 70)
        print("2) أرصدة حسابات النقدية/البنوك في دفتر الحسابات (1110/1120)")
        print("=" * 70)
        for code in ("1110", "1120"):
            acc = (await db.execute(select(ChartOfAccount).where(ChartOfAccount.code == code))).scalar_one_or_none()
            if not acc:
                print(f"  حساب {code}: غير موجود")
                continue
            debit = (await db.execute(
                select(func.coalesce(func.sum(AccountingEntryLine.amount), 0)).where(
                    AccountingEntryLine.account_id == acc.id, AccountingEntryLine.side == "debit",
                )
            )).scalar() or Decimal(0)
            credit = (await db.execute(
                select(func.coalesce(func.sum(AccountingEntryLine.amount), 0)).where(
                    AccountingEntryLine.account_id == acc.id, AccountingEntryLine.side == "credit",
                )
            )).scalar() or Decimal(0)
            gl_balance = Decimal(str(debit)) - Decimal(str(credit))
            print(f"  [{code}] {acc.name_ar}: مدين={debit} | دائن={credit} | الرصيد={gl_balance}")

        print()
        print("=" * 70)
        print("3) الفجوة الإجمالية: مجموع أرصدة الخزائن (cash+bank) - مجموع رصيد 1110+1120")
        print("=" * 70)
        total_treasury = sum(Decimal(str(t.balance)) for t in treasuries)
        gl_total = Decimal(0)
        for code in ("1110", "1120"):
            acc = (await db.execute(select(ChartOfAccount).where(ChartOfAccount.code == code))).scalar_one_or_none()
            if not acc:
                continue
            debit = (await db.execute(
                select(func.coalesce(func.sum(AccountingEntryLine.amount), 0)).where(
                    AccountingEntryLine.account_id == acc.id, AccountingEntryLine.side == "debit",
                )
            )).scalar() or Decimal(0)
            credit = (await db.execute(
                select(func.coalesce(func.sum(AccountingEntryLine.amount), 0)).where(
                    AccountingEntryLine.account_id == acc.id, AccountingEntryLine.side == "credit",
                )
            )).scalar() or Decimal(0)
            gl_total += Decimal(str(debit)) - Decimal(str(credit))
        gap = total_treasury - gl_total
        print(f"  إجمالي أرصدة الخزائن = {total_treasury}")
        print(f"  إجمالي رصيد 1110+1120 في الحسابات = {gl_total}")
        print(f"  الفجوة = {gap}")

        print()
        print("=" * 70)
        print("4) كل الحسابات الشاذة (الرصيد المُعدَّل العلامة بعكس normal_balance)")
        print("=" * 70)
        accounts = (await db.execute(select(ChartOfAccount).where(ChartOfAccount.is_active == True))).scalars().all()
        for acc in accounts:
            debit = (await db.execute(
                select(func.coalesce(func.sum(AccountingEntryLine.amount), 0)).where(
                    AccountingEntryLine.account_id == acc.id, AccountingEntryLine.side == "debit",
                )
            )).scalar() or Decimal(0)
            credit = (await db.execute(
                select(func.coalesce(func.sum(AccountingEntryLine.amount), 0)).where(
                    AccountingEntryLine.account_id == acc.id, AccountingEntryLine.side == "credit",
                )
            )).scalar() or Decimal(0)
            if debit == 0 and credit == 0:
                continue
            raw = Decimal(str(debit)) - Decimal(str(credit))
            signed = raw if acc.normal_balance == "debit" else -raw
            if signed < 0:
                print(f"  ⚠️ [{acc.code}] {acc.name_ar} ({acc.account_type}/{acc.normal_balance}): مدين={debit} دائن={credit} رصيد_خام={raw} رصيد_معدَّل={signed}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
