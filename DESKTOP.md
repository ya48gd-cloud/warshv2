# Heavy ERP Desktop

تشغيل نسخة Electron بدون Docker.

## المتطلبات

- Node.js + npm
- Python 3.11 أو أحدث

## التشغيل

```bash
npm install
npm run desktop
```

من شاشة البرنامج:

1. اضغط `Setup`.
2. اترك خيار البيانات التجريبية مفعلا لأول تشغيل.
3. بعد انتهاء الإعداد اضغط `Open App`.

بيانات الدخول الافتراضية:

```text
admin / admin123
```

## أين تحفظ البيانات؟

Electron يحفظ قاعدة SQLite وملفات الرفع داخل مجلد بيانات التطبيق الخاص بالمستخدم، وليس داخل Docker.

## ملاحظات

- نسخة Docker ما زالت موجودة كما هي.
- نسخة Desktop تستخدم SQLite محليًا، لذلك لا تحتاج PostgreSQL أو Redis.
- الباك إند يقدم ملفات React المبنية من `react-src/dist` عند تشغيله من Electron.
