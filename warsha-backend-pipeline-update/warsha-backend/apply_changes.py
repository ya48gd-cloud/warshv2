"""
Run this from E:\heavy-erp-fixed\warsha\backend
python apply_changes.py
"""
import os, shutil

# 1. Copy super_admin.py
shutil.copy('super_admin.py', 'app/api/super_admin.py')
print('✅ super_admin.py copied to app/api/')

# 2. Secure signup in auth.py
auth = open('app/api/auth.py', encoding='utf-8').read()
if 'signup_blocked' not in auth:
    auth = auth.replace(
        '@router.post("/signup", status_code=201)\nasync def signup(',
        '@router.post("/signup", status_code=201)\nasync def signup_blocked():\n    raise HTTPException(403, "التسجيل متاح فقط عبر Super Admin")\n\n@router.post("/signup-internal", status_code=201)\nasync def signup('
    )
    open('app/api/auth.py', 'w', encoding='utf-8').write(auth)
    print('✅ auth.py - signup secured')
else:
    print('ℹ️  auth.py already secured')

# 3. Add router to main.py
main = open('app/main.py', encoding='utf-8').read()
if 'super_admin_router' not in main:
    main = main.replace(
        'from app.api.warranty_certs',
        'from app.api.super_admin      import router as super_admin_router\nfrom app.api.warranty_certs'
    )
    main = main.replace(
        'app.include_router(warranty_print_router,  prefix="/api/v1")',
        'app.include_router(warranty_print_router,  prefix="/api/v1")\napp.include_router(super_admin_router,       prefix="/api/v1")'
    )
    open('app/main.py', 'w', encoding='utf-8').write(main)
    print('✅ main.py - router added')
else:
    print('ℹ️  main.py already has super_admin_router')

print('\n✅ Backend changes done! Now run: git add . && git commit -m "add super admin" && git push')
