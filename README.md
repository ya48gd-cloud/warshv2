# Heavy ERP — ورشة المعدات الثقيلة

نظام ERP متكامل للورش الصناعية — React 18 + FastAPI + PostgreSQL + nginx

---

## متطلبات التشغيل

- Docker Engine 24+
- Docker Compose v2
- Make (اختياري)

---

## أول تشغيل (من الصفر)

```bash
git clone https://github.com/ya48gd-cloud/warshv2.git
cd warshv2
make setup
```

ثم افتح: **http://localhost**

---

## أوامر يومية

| الأمر | الوظيفة |
|-------|---------|
| `make up` | تشغيل جميع الخدمات |
| `make down` | إيقاف جميع الخدمات |
| `make fe` | إعادة بناء React بعد التعديل |
| `make fe-dev` | تشغيل React dev server محلياً |
| `make build` | إعادة بناء backend image |
| `make seed` | حقن بيانات تجريبية |
| `make migrate` | تشغيل Alembic migrations |
| `make logs` | متابعة logs الـ backend |
| `make test` | اختبار سريع للـ API |
| `make status` | حالة الحاويات |
| `make clean` | حذف كل شيء بما في ذلك البيانات |

---

## بدون make

```bash
# أول تشغيل
docker compose --profile build up frontend-builder --exit-code-from frontend-builder
docker compose up -d
docker compose exec backend alembic upgrade head
docker compose exec backend python seed.py

# تشغيل عادي
docker compose up -d

# إعادة بناء الـ frontend
docker compose --profile build up frontend-builder --exit-code-from frontend-builder
docker compose exec nginx nginx -s reload

# إعادة بناء الـ backend
docker compose build --no-cache backend && docker compose restart backend
```

---

## بيانات الدخول التجريبية

| المستخدم | كلمة المرور | الصلاحيات |
|----------|-------------|-----------|
| `admin` | `admin123` | وصول كامل |
| `accountant` | `accountant123` | المبيعات والرواتب |
| `production` | `production123` | المخزون والإنتاج |
| `viewer` | `viewer123` | قراءة فقط |

---

## تطوير الـ Frontend (React)

```bash
# Hot reload محلياً (الأسرع)
docker compose up -d db redis backend nginx
cd react-src && npm install && npm run dev
# http://localhost:3000
```

---

## الخدمات والمنافذ

| الخدمة | المنفذ |
|--------|--------|
| nginx (frontend + proxy) | 80 |
| FastAPI backend | 8000 |
| PostgreSQL | 5432 |
| Redis | 6379 |
| Swagger UI | http://localhost/docs |

---

## متغير مهم في الإنتاج

```yaml
JWT_SECRET: change-me-in-production-use-a-long-random-string
```
