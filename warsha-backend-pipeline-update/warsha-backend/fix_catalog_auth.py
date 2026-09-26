"""
Fix: link catalog.py token verification to super_admin.py login tokens
Run from: E:\heavy-erp-fixed\warsha\backend
python fix_catalog_auth.py
"""

# Fix catalog.py to import tokens from super_admin instead of having its own set
path = 'app/api/catalog.py'
c = open(path, encoding='utf-8').read()

old = '''SUPER_ADMIN_USERNAME = os.getenv("SUPER_ADMIN_USERNAME", "superadmin")
SUPER_ADMIN_PASSWORD = os.getenv("SUPER_ADMIN_PASSWORD", "changeme123")
_sa_tokens: set = set()

def register_token(token: str):
    _sa_tokens.add(token)'''

new = '''from app.api.super_admin import _active_tokens as _sa_tokens'''

if old in c:
    c = c.replace(old, new)
    # also fix the verify function reference
    c = c.replace(
        'def _verify_sa(authorization: Optional[str] = Header(None)):\n    if not authorization or not authorization.startswith("Bearer "):\n        raise HTTPException(401, "Super Admin فقط")\n    token = authorization.split(" ", 1)[1]\n    if token not in _sa_tokens:\n        raise HTTPException(401, "جلسة منتهية")\n    return token',
        'def _verify_sa(authorization: Optional[str] = Header(None)):\n    if not authorization or not authorization.startswith("Bearer "):\n        raise HTTPException(401, "Super Admin فقط")\n    token = authorization.split(" ", 1)[1]\n    if token not in _sa_tokens:\n        raise HTTPException(401, "جلسة منتهية — سجل دخول مرة أخرى")\n    return token'
    )
    open(path, 'w', encoding='utf-8').write(c)
    print('✅ catalog.py — now shares tokens with super_admin login')
else:
    print('⚠️  Pattern not found — checking current state...')
    if '_active_tokens' in c:
        print('ℹ️  Already fixed')
    else:
        print('❌ Manual check needed')

print('\nDone! Now run: git add . && git commit -m "fix catalog auth token sharing" && git push')
