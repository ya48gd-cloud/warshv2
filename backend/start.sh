#!/bin/sh
set -e

echo "=== Waiting for database ==="
until python -c "
import asyncio, asyncpg
async def check():
    conn = await asyncpg.connect('postgresql://erp_user:erp_pass@db:5432/heavy_erp')
    await conn.close()
asyncio.run(check())
" 2>/dev/null; do
  echo "DB not ready, retrying in 3s..."
  sleep 3
done
echo "=== Database ready ==="

echo "=== Running migrations ==="
alembic upgrade head

echo "=== Starting server ==="
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
