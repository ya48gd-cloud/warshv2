"""
رفع 153 مادة هندسية حقيقية (صاج، مواسير، زوايا، حديد، إلخ) للكتالوج العام
يستخدم Super Admin API على السيرفر المحلي
Run from: warsha-backend-main/
python seed_catalog.py
"""
import json
import os
import urllib.request
import urllib.error

API_URL = os.getenv("WARSHA_API_URL", "http://localhost:8000")
SA_USERNAME = input("Super Admin Username: ").strip()
SA_PASSWORD = input("Super Admin Password: ").strip()

def post_json(url, data, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"❌ HTTP {e.code}: {body}")
        return None

# ── 1. Login as Super Admin ─────────────────────────────────
print("\n🔐 Logging in as Super Admin...")
login_res = post_json(f"{API_URL}/api/v1/super-admin/login", {
    "username": SA_USERNAME, "password": SA_PASSWORD
})
if not login_res or "token" not in login_res:
    print("❌ Login failed. Check credentials.")
    exit(1)

token = login_res["token"]
print("✅ Logged in successfully")

# ── 2. Load catalog items ───────────────────────────────────
with open("catalog_items_v2.json", encoding="utf-8") as f:
    items = json.load(f)

print(f"\n📦 Found {len(items)} items to import")

# ── 3. Import items one-by-one (to support engineering fields per item) ──
added, skipped, failed = 0, 0, 0
for i, item in enumerate(items, 1):
    res = post_json(f"{API_URL}/api/v1/catalog", item, token=token)
    if res and "id" in res:
        added += 1
        status = "✅"
    elif res and "موجود" in str(res.get("detail", "")):
        skipped += 1
        status = "⏭️"
    else:
        failed += 1
        status = "❌"
    print(f"{status} [{i}/{len(items)}] {item['code']} — {item['name_ar']}")

print("\n" + "="*50)
print(f"✅ Added:   {added}")
print(f"⏭️  Skipped: {skipped} (already exist)")
print(f"❌ Failed:  {failed}")
print("="*50)
print("\nDone! Check the catalog in Super Admin panel: /super-admin → 📦 الكتالوج العام")
