# ═══════════════════════════════════════════════════════════════
#  Makefile — Heavy Equipment Workshop ERP (React Edition)
#  Usage: make <command>
# ═══════════════════════════════════════════════════════════════

.PHONY: up down build build-frontend seed logs shell-backend shell-db \
        migrate rollback test clean restart dev

# ── Full start (assumes frontend already built) ─────────────────
up:
	docker compose up -d
	@echo ""
	@echo "✅  ERP is running"
	@echo "    Frontend  →  http://localhost"
	@echo "    API docs  →  http://localhost/docs"
	@echo ""

# ── Build React frontend, then start everything ─────────────────
start: build-frontend up

# ── Build React frontend inside Docker (no local Node needed) ──
build-frontend:
	@echo "⚛️  Building React frontend..."
	docker compose --profile build run --rm frontend-builder
	@echo "✅  Frontend built → ./frontend/"

# ── Rebuild backend image (after Python code changes) ──────────
build:
	docker compose build --no-cache backend

# ── Rebuild everything ──────────────────────────────────────────
build-all: build build-frontend

# ── Stop all services ───────────────────────────────────────────
down:
	docker compose down

# ── Run DB migrations ───────────────────────────────────────────
migrate:
	docker compose exec backend alembic upgrade head

# ── Rollback last migration ─────────────────────────────────────
rollback:
	docker compose exec backend alembic downgrade -1

# ── Seed demo data + RBAC users ────────────────────────────────
seed:
	docker compose exec backend python seed.py

# ── View backend logs ───────────────────────────────────────────
logs:
	docker compose logs -f backend

logs-all:
	docker compose logs -f

# ── Open shells ─────────────────────────────────────────────────
shell-backend:
	docker compose exec backend bash

shell-db:
	docker compose exec db psql -U erp_user -d heavy_erp

# ── Restart backend only (after Python edits) ──────────────────
restart:
	docker compose restart backend nginx

# ── Local React dev server (needs Node on host) ────────────────
dev:
	@echo "Starting React dev server on http://localhost:3000"
	@echo "(Backend must be running: make up)"
	cd react-src && npm install && npm run dev

# ── Quick health check ──────────────────────────────────────────
test:
	@echo "=== Health ==="
	curl -s http://localhost/health | python3 -m json.tool
	@echo "=== Auth login ==="
	curl -s -X POST http://localhost/api/v1/auth/login \
	  -H "Content-Type: application/json" \
	  -d '{"username":"admin","password":"admin123"}' | python3 -m json.tool

# ── Full reset (delete all data volumes) ───────────────────────
clean:
	docker compose down -v
	@echo "⚠️  All data volumes deleted"

# ── First-time setup ────────────────────────────────────────────
setup: build-frontend up
	@sleep 5
	@echo "⏳  Waiting for DB..."
	docker compose exec backend alembic upgrade head
	docker compose exec backend python seed.py
	@echo ""
	@echo "🎉  Setup complete!"
	@echo "    Open http://localhost"
	@echo "    Login: admin / admin123"
	@echo ""

# ── Tests ────────────────────────────────────────────────────────
test-unit:
	@echo "🧪 Running unit + integration tests (in-memory SQLite, no Docker needed)..."
	cd backend && pip install -r requirements-test.txt -q && \
	PYTHONPATH=. python -m pytest tests/ -v --tb=short

test-docker:
	@echo "🧪 Running tests inside Docker container..."
	docker compose exec backend sh -c "cd /app && pip install -r requirements-test.txt -q && PYTHONPATH=. python -m pytest tests/ -v --tb=short"

test-ci:
	@echo "🧪 CI test run (quiet, fail-fast)..."
	cd backend && PYTHONPATH=. python -m pytest tests/ -q --tb=line -x
