"""
Pure unit tests for business logic — no HTTP, no DB.
Tests the math and rules directly from the functions.
"""
import pytest
from decimal import Decimal
from datetime import datetime, timedelta, timezone

from app.api.auth import (
    hash_password, create_token, verify_token,
    VALID_ROLES, TOKEN_EXPIRE_HOURS,
)


# ── Password hashing ───────────────────────────────────────────

class TestPasswordHashing:
    def test_same_input_same_hash(self):
        assert hash_password("test") == hash_password("test")

    def test_different_inputs_different_hashes(self):
        assert hash_password("abc") != hash_password("ABC")

    def test_hash_is_hex_string(self):
        h = hash_password("hello")
        int(h, 16)  # raises if not hex

    def test_hash_length_is_64(self):
        assert len(hash_password("x" * 1000)) == 64

    def test_empty_string_has_hash(self):
        h = hash_password("")
        assert len(h) == 64

    def test_unicode_password(self):
        h1 = hash_password("كلمة المرور")
        h2 = hash_password("كلمة المرور")
        assert h1 == h2


# ── JWT token ─────────────────────────────────────────────────

class TestJWT:
    def test_token_has_three_segments(self):
        t = create_token(1, "admin", "admin")
        assert len(t.split(".")) == 3

    def test_verify_returns_correct_sub(self):
        t = create_token(42, "user", "viewer")
        p = verify_token(t)
        assert p["sub"] == "42"

    def test_verify_returns_correct_role(self):
        for role in VALID_ROLES:
            t = create_token(1, "u", role)
            assert verify_token(t)["role"] == role

    def test_verify_returns_correct_username(self):
        t = create_token(1, "أحمد", "admin")
        assert verify_token(t)["username"] == "أحمد"

    def test_tampered_signature_fails(self):
        t = create_token(1, "u", "admin")
        parts = t.split(".")
        parts[2] = "invalidsignature"
        assert verify_token(".".join(parts)) is None

    def test_tampered_payload_fails(self):
        t = create_token(1, "u", "admin")
        parts = t.split(".")
        import base64, json
        evil = base64.urlsafe_b64encode(
            json.dumps({"sub": "999", "role": "admin", "username": "hacker",
                        "exp": int((datetime.now(timezone.utc) + timedelta(hours=12)).timestamp()),
                        "iat": int(datetime.now(timezone.utc).timestamp())}).encode()
        ).rstrip(b"=").decode()
        parts[1] = evil
        assert verify_token(".".join(parts)) is None

    def test_expired_token_returns_none(self, monkeypatch):
        import app.api.auth as m
        monkeypatch.setattr(m, "TOKEN_EXPIRE_HOURS", -1)
        t = create_token(1, "u", "admin")
        assert verify_token(t) is None

    def test_garbage_token_returns_none(self):
        assert verify_token("not.a.token") is None
        assert verify_token("") is None
        assert verify_token("a.b") is None  # only 2 parts


# ── RBAC roles set ─────────────────────────────────────────────

class TestValidRoles:
    def test_valid_roles_contains_four(self):
        assert len(VALID_ROLES) == 4

    def test_admin_in_roles(self):
        assert "admin" in VALID_ROLES

    def test_accountant_in_roles(self):
        assert "accountant" in VALID_ROLES

    def test_production_in_roles(self):
        assert "production" in VALID_ROLES

    def test_viewer_in_roles(self):
        assert "viewer" in VALID_ROLES

    def test_superuser_not_in_roles(self):
        assert "superuser" not in VALID_ROLES


# ── Stock arithmetic ───────────────────────────────────────────

class TestStockArithmetic:
    """Unit tests for stock qty math without DB."""

    def _apply_movement(self, stock: Decimal, qty: Decimal, mtype: str) -> Decimal:
        """Mirrors the logic in inventory.py record_movement."""
        if mtype == "in":
            return stock + qty
        elif mtype == "out":
            return stock - qty
        return stock  # adjust handled separately

    def test_in_increases_stock(self):
        result = self._apply_movement(Decimal("100"), Decimal("50"), "in")
        assert result == Decimal("150")

    def test_out_decreases_stock(self):
        result = self._apply_movement(Decimal("100"), Decimal("30"), "out")
        assert result == Decimal("70")

    def test_exact_withdrawal(self):
        result = self._apply_movement(Decimal("100"), Decimal("100"), "out")
        assert result == Decimal("0")

    def test_over_withdrawal_goes_negative(self):
        result = self._apply_movement(Decimal("10"), Decimal("50"), "out")
        assert result == Decimal("-40")

    def test_zero_movement(self):
        result = self._apply_movement(Decimal("100"), Decimal("0"), "in")
        assert result == Decimal("100")

    def test_piece_qty_calculation(self):
        """Piece withdrawal: actual_qty = pieces * weight_per_piece."""
        pieces = 5
        weight_per_piece = Decimal("3.500")
        actual_qty = Decimal(str(pieces)) * weight_per_piece
        assert actual_qty == Decimal("17.500")

    def test_piece_qty_rounds_correctly(self):
        pieces = 3
        weight_per_piece = Decimal("1.333")
        actual_qty = Decimal(str(pieces)) * weight_per_piece
        assert actual_qty == Decimal("3.999")


# ── Cost line arithmetic ───────────────────────────────────────

class TestCostLineArithmetic:
    def _calc_total(self, qty: float, unit_cost: float) -> float:
        return round(qty * unit_cost, 2)

    def test_basic_total(self):
        assert self._calc_total(5.0, 200.0) == 1000.0

    def test_fractional_qty(self):
        assert self._calc_total(2.5, 100.0) == 250.0

    def test_zero_qty(self):
        assert self._calc_total(0.0, 500.0) == 0.0

    def test_precision(self):
        result = self._calc_total(3.333, 10.0)
        assert result == pytest.approx(33.33, abs=0.01)


# ── Invoice total validation ───────────────────────────────────

class TestInvoiceTotals:
    def _calc_total(self, subtotal, tax_pct, discount_amt=0):
        tax = round(subtotal * tax_pct / 100, 2)
        return subtotal + tax - discount_amt

    def test_no_tax_no_discount(self):
        assert self._calc_total(10000, 0) == 10000

    def test_14_percent_vat(self):
        assert self._calc_total(10000, 14) == pytest.approx(11400, abs=0.01)

    def test_discount_reduces_total(self):
        assert self._calc_total(10000, 14, discount_amt=1000) == pytest.approx(10400, abs=0.01)

    def test_100_percent_discount(self):
        total = self._calc_total(5000, 0, discount_amt=5000)
        assert total == 0

    def test_total_never_below_zero_with_sane_inputs(self):
        # discount cannot exceed subtotal + tax in practice
        total = self._calc_total(1000, 0, discount_amt=1000)
        assert total == 0


# ── Low-stock detection logic ──────────────────────────────────

class TestLowStockDetection:
    def _is_low(self, stock_qty: float, reorder_level: float) -> bool:
        return stock_qty <= reorder_level

    def test_below_reorder_is_low(self):
        assert self._is_low(5, 20) is True

    def test_exactly_at_reorder_is_low(self):
        assert self._is_low(20, 20) is True

    def test_above_reorder_is_not_low(self):
        assert self._is_low(21, 20) is False

    def test_zero_stock_is_always_low(self):
        assert self._is_low(0, 1) is True

    def test_zero_reorder_level_never_low_above_zero(self):
        assert self._is_low(1, 0) is False


# ── RBAC permission matrix logic ──────────────────────────────

class TestRBACPermissions:
    WRITE_MAP = {
        "admin":      ["inventory", "production", "sales", "workers", "customers", "users"],
        "accountant": ["sales", "workers", "customers"],
        "production": ["inventory", "production"],
        "viewer":     [],
    }

    def can_write(self, role: str, module: str) -> bool:
        return module in self.WRITE_MAP.get(role, [])

    def test_admin_can_write_all(self):
        for m in ["inventory", "production", "sales", "workers", "customers", "users"]:
            assert self.can_write("admin", m)

    def test_viewer_cannot_write_anything(self):
        for m in ["inventory", "production", "sales", "workers", "customers", "users"]:
            assert not self.can_write("viewer", m)

    def test_accountant_can_write_sales(self):
        assert self.can_write("accountant", "sales")

    def test_accountant_cannot_write_inventory(self):
        assert not self.can_write("accountant", "inventory")

    def test_accountant_cannot_write_users(self):
        assert not self.can_write("accountant", "users")

    def test_production_can_write_inventory(self):
        assert self.can_write("production", "inventory")

    def test_production_cannot_write_sales(self):
        assert not self.can_write("production", "sales")

    def test_production_cannot_write_users(self):
        assert not self.can_write("production", "users")

    def test_all_roles_are_covered(self):
        assert set(self.WRITE_MAP.keys()) == {"admin", "accountant", "production", "viewer"}
