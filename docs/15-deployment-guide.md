# 15 — التشغيل من بيئة نظيفة

جُرّبت هذه الخطوات حرفيًا من مجلد فارغ في المرحلة 10 (Python 3.13، Linux)، وعلى Windows بواسطة نوف (PowerShell).

```bash
git clone <المستودع> covercast && cd covercast    # أو فك الملف المضغوط
python -m venv .venv
source .venv/bin/activate                          # Windows: .venv\Scripts\activate
pip install -r requirements.txt                    # Django 5.2 فقط
python manage.py migrate
python manage.py load_demo
python manage.py test                              # يجب: OK
python manage.py create_supervisor nouf --name "نوف"  # كلمة المرور تُسأل مرتين
python manage.py runserver                         # http://127.0.0.1:8000/
```

لإنشاء المشرف بلا سؤال (في سكربت): ضعي كلمة المرور في `COVERCAST_SUPERVISOR_PASSWORD` مؤقتًا. لا تُكتب في الأمر نفسه ولا في الكود.

## متغيرات البيئة

| المتغير | الافتراضي | متى يلزم |
|---|---|---|
| `COVERCAST_DEBUG` | `1` | `0` لأي تشغيل غير محلي |
| `COVERCAST_SECRET_KEY` | — | **إلزامي** عند `COVERCAST_DEBUG=0`، وإلا يرفض الإقلاع |
| `COVERCAST_ALLOWED_HOSTS` | `localhost,127.0.0.1` | عند العرض من جهاز آخر |
| `COVERCAST_HTTPS` | — | `1` فقط خلف HTTPS حقيقي: ملفات ارتباط آمنة، وتحويل إلى HTTPS، وHSTS (A-30) |
| `COVERCAST_SUPERVISOR_PASSWORD` | — | اختياري لـ`create_supervisor` في سكربت، ثم يُحذف |
| `COVERCAST_BOARD_POLL_MS` | `5000` | كل كم ملّي ثانية تسأل اللوحة الخادم هل تغيّر شيء (`/board/live`) |

## العرض بلا إنترنت

- بعد `pip install` لا يحتاج النظام إنترنت.
- الخط (IBM Plex Sans Arabic، رخصة OFL) والـCSS والـJS كلها محلية في `coverage/static/coverage/`.
- اختبار `test_no_external_address_in_templates_css_or_js` يفشل إن أضاف أحد رابطًا خارجيًا (CDN أو Google Fonts).
- تحقق في متصفح حقيقي: صفر طلبات خارج الخادم.

## قبل العرض

`python manage.py load_demo --reset` يعيد البيانات إلى حالتها الأولى، ويمحو أي اعتماد جُرّب في التمرين.

## العرض أمام اللجنة بوضع الإنتاج (موصى به)

مع `DEBUG=0` تظهر صفحات خطأ عربية مختصرة، بدل صفحة Django الصفراء التي تعرض المسارات والإعدادات.

```powershell
$env:COVERCAST_DEBUG = "0"
$env:COVERCAST_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(50))"
python manage.py runserver --insecure
```

`--insecure` يجعل `runserver` يقدّم ملفات CSS والخط مع `DEBUG=0`. هذا مناسب لعرض محلي فقط، لا لخادم حقيقي.

**تحقق الأمان:** `manage.py check --deploy` مع `COVERCAST_HTTPS=1` لا يترك إلا W005 وW021. هما قرار HSTS للنطاقات الفرعية وقوائم المتصفحات، ويعودان لمالك النطاق، فتُركا ظاهرين لا مخفيين.

## كل الأوامر القابلة لإعادة الإنتاج (محدّث حتى P0، 15 أكتوبر 2026)

بيئتان منفصلتان: Django لا يحتاج مكتبات التنبؤ، والتنبؤ لا يحتاج Django ([ADR 0009](adr/0009-forecast-outside-django.md)).

### 1. Django: الإعداد والاختبار

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt                       # اختياري: Playwright لاختبار المتصفح
python manage.py migrate                                  # يطبّق 0001…0004
python manage.py test                                     # المتوقع: Ran 305 tests … OK (skipped=4 بلا Playwright)
```

بلا Playwright، تُتخطى اختبارات المتصفح ويُطبع السبب.

**المظهر:** الواجهة فاتحة افتراضيًا كتصميم شهد، مهما كان مظهر Windows أو المتصفح. زر «الوضع الداكن / الوضع الفاتح» في الشريط العلوي بجانب اسم المستخدم، والاختيار يُحفظ في هذا المتصفح فقط (`localStorage`، المفتاح `covercast-theme`). للعودة إلى الفاتح: اضغط الزر، أو امسح بيانات الموقع.

### 2. بيانات العرض

```bash
python manage.py load_demo --reset                              # v0.2 فقط: G1 وG2
python manage.py load_demo --reset --scenario riyadh_peak       # + يوم الرياض المحاكى (24 أكتوبر 2030)
python manage.py import_forecast forecast/out/export/osaka_replay.json   # إعادة عرض أوساكا الرسمية
python manage.py import_forecast forecast/out/export/osaka_replay.json   # مرة ثانية: "Already imported"
```

**الترتيب مهم:** `load_demo --reset` يمحو التنبؤات المستوردة. فإن أُعيد التحميل، يُعاد `import_forecast` بعده.

### 3. التنبؤ: التحقق من كل نتيجة محفوظة

```bash
python -m venv .venv-forecast && source .venv-forecast/bin/activate
pip install -r requirements-forecast.txt
python forecast/build_series.py --check           # F1: 184 يومًا مطابقة للأخبار الرسمية
python forecast/evaluate.py --check               # F2: النتائج تُعاد حرفيًا
python forecast/evaluate.py --stage f3 --check    # F3: النماذج الثلاثة على طيّات الاختيار
python forecast/freeze.py --check                 # F4b: التجميد صالح (seasonal_naive)
python forecast/evaluate.py --final --check       # F4c: التقييم النهائي يُعاد حرفيًا (لا يشغّل Chronos-2)
python forecast/export_forecast.py --check        # P0-3: ملف إعادة العرض يُعاد حرفيًا
python -m unittest discover -s forecast/tests     # المتوقع: Ran 121 tests … OK (skipped=2)
```

**أوامر لا تُشغَّل مرة أخرى:**
- `run_chronos_local.py --final` و`evaluate.py --final` بلا `--check`: الاختبار النهائي شُغّل مرة واحدة (F4c).
- `freeze.py --write`: التجميد يُكتب مرة واحدة.

### نهايات الأسطر على Windows (E-18)

مع `core.autocrlf=true`، وهو الإعداد الافتراضي في Git for Windows، تُسحب الملفات النصية بـCRLF.

| الملفات | ماذا يحدث على Windows | لماذا لا يضر |
|---|---|---|
| `data/**` (حزمة العرض، والسيناريو) | **تبقى كما هي بالبايت**: `.gitattributes` فيه `data/** -text` | اختبارات المصدر تقارن بالبايت (E-05) |
| `forecast/out/**` (ومنه `osaka_replay.json`)، و`forecast/*.py`، والبروتوكول | تُسحب بـCRLF | كل بصمة تُحسب بعد توحيد CRLF إلى LF: التجميد (`freeze.sha256_lf`)، و`--check` لكل نتيجة، والاستيراد (`forecast_import.sha256_lf`). فالملف بـLF وبـCRLF ملف واحد |

- **ما يُطبَّع:** `\r\n` إلى `\n` فقط. أي تغيير آخر في البايتات (`\r` وحدها، أو `\r\r\n`، أو سطر زائد) محتوى مختلف، ويُرفض استيراده تحت رمز التشغيل نفسه إلا بـ`--replace`.
- **`skipped=2`** في `manage.py test` هما اختبارا المتصفح حين لا يكون Playwright مثبتًا. هذا متوقع.

### 4. فحص الوثائق

```bash
python tools/check_docs.py      # كل مسار مذكور موجود، وأرقام F2/F3/F4c في الوثائق = ملفات النتائج
```
