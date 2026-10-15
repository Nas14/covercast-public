# 13 — دليل التكامل

## تحديث بيانات العرض

1. **لا تعدّلي `data/demo/v0.1/`.** هي حزمة رغد كما سُلّمت.
2. لتغيير قيمة (أهمية موقع مثلًا): عدّلي الثابت في `data/build_v02.py` (`SITE_ATTRS`)، ثم:
   ```bash
   python data/build_v02.py          # يعيد كتابة v0.2
   python manage.py test             # يتأكد أن شيئًا لم ينكسر
   python manage.py load_demo --reset
   ```
3. **لإصلاح جديد:** دالة جديدة في `build_v02.py` تُضاف إلى `FIXES`، وسطر في جدول `data/demo/README.md`.

## إضافة حزمة بيانات مختلفة كليًا

`python manage.py load_demo --reset --path path/to/pack`

الحزمة يجب أن تحتوي الملفات الأربعة عشر بالأعمدة في `coverage/importer.py` → `EXPECTED_COLUMNS`. الأعمدة الإضافية مسموحة وتُتجاهل. والمستورد يطبع كل مشكلة بصيغة `ملف:سطر` ولا يغيّر شيئًا إن وُجدت مشكلة.

## ربط واجهة

- العقود في `06-api-contracts.md`، ومنفذة في `coverage/api/`. اختبار `ApiContractTests` يفشل إن تغيّر اسم حقل.
- الرموز الثابتة (حالات التكليف، رموز الاستبعاد، مستويات الحضور) في `coverage/codes.py`، مع تسمياتها العربية.

## التنبؤ (اختياري)

العقد في التقرير §9 (`forecast.json`). لا يوجد أمر استيراد بعد: المرحلة 7 اختيارية ولم تُبنَ. المحرك يقرأ `AttendanceForecast` إن وُجد، وغيابه يعني `volume_uplift = 0`.
