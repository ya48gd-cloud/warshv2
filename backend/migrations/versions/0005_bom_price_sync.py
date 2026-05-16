"""BOM price sync function and trigger

Revision ID: 0005
Revises: 0004
Create Date: 2024-01-05
"""
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Function to recalculate BOM line costs when material price changes
    op.execute("""
        CREATE OR REPLACE FUNCTION sync_bom_prices()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.unit_cost IS DISTINCT FROM NEW.unit_cost THEN
                UPDATE bom_lines
                SET unit_cost  = NEW.unit_cost,
                    total_cost = qty * NEW.unit_cost
                WHERE material_id = NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE OR REPLACE TRIGGER trg_bom_price_sync
        AFTER UPDATE ON materials
        FOR EACH ROW
        EXECUTE FUNCTION sync_bom_prices();
    """)

    # Price change history log
    op.execute("""
        CREATE TABLE IF NOT EXISTS material_price_history (
            id SERIAL PRIMARY KEY,
            material_id INTEGER NOT NULL REFERENCES materials(id),
            old_price NUMERIC(14,2),
            new_price NUMERIC(14,2),
            changed_at TIMESTAMP DEFAULT NOW(),
            notes TEXT
        );
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION log_price_change()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.unit_cost IS DISTINCT FROM NEW.unit_cost THEN
                INSERT INTO material_price_history(material_id, old_price, new_price)
                VALUES (NEW.id, OLD.unit_cost, NEW.unit_cost);
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE OR REPLACE TRIGGER trg_price_history
        AFTER UPDATE ON materials
        FOR EACH ROW
        EXECUTE FUNCTION log_price_change();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_bom_price_sync ON materials")
    op.execute("DROP TRIGGER IF EXISTS trg_price_history ON materials")
    op.execute("DROP FUNCTION IF EXISTS sync_bom_prices()")
    op.execute("DROP FUNCTION IF EXISTS log_price_change()")
    op.execute("DROP TABLE IF EXISTS material_price_history")
