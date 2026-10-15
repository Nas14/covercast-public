# 02 — المعمارية

المخطط الأول وتوزيع المسؤوليات في `phase-0-audit.md` §5. هنا ما نُفذ فعلًا، محدّثًا حتى P0 (15 أكتوبر 2026).

## النظام كاملًا

جزآن منفصلان، يربطهما **ملف** لا استيراد كود ([ADR 0009](adr/0009-forecast-outside-django.md)):

```mermaid
flowchart TB
    subgraph OFF["forecast/ (خارج Django)"]
        RAW["26 خبرًا رسميًا<br/>forecast/data/raw/official/"] --> SER["build_series.py<br/>osaka_daily.csv (184 يومًا)"]
        SER --> EVAL["evaluate.py<br/>طيّات زمنية · مقاييس · قاعدة الاختيار"]
        CH["Chronos-2<br/>على جهاز نوف"] -->|"تنبؤات + سجل تشغيل"| EVAL
        EVAL --> FRZ["selection_frozen.json<br/>seasonal_naive"]
        FRZ --> EXP["export_forecast.py"]
        EVAL --> EXP
    end
    EXP -->|"osaka_replay.json<br/>(رسمي)"| IMP
    SCN["data/demo/scenario_riyadh_peak/<br/>forecast.json (محاكى)"] --> IMP
    subgraph DJ["Django: covercast"]
        IMP["import_forecast<br/>forecast_import.py"] --> DB[("SQLite")]
        PACK["load_demo<br/>data/demo/v0.2"] --> DB
        DB --> SNAP["services/snapshot.py<br/>(القارئ الوحيد للمحرك)"]
        SNAP --> ENG["services: demand → gaps →<br/>eligibility → ranking → baseline"]
        ENG --> API["api/ (JSON)"]
        ENG --> PAGES["views.py + قوالب عربية"]
        PAGES --> APPR["approval.approve<br/>offers.respond<br/>absence.report_absence"]
        API --> APPR
        APPR --> DB
    end
```

## تدفق البيانات: من مستوى الزوار إلى خانة مغطاة

```mermaid
flowchart LR
    L["مستوى اليوم<br/>(تشغيل فعّال)"] --> R["DemandRule<br/>(نوع الموقع، المستوى)"]
    R --> V["volume_uplift"]
    B["Site.base_staff"] --> REQ["required =<br/>base + event + volume"]
    E["EventImpact<br/>(وفد، كبار شخصيات،<br/>أو يوم وطني بلا تنبؤ)"] --> REQ
    V --> REQ
    REQ --> G["detect_gaps<br/>مقارنة بالحاضرين"]
    A["Assignment فعّال<br/>ناقص الغياب"] --> G
    G -->|"خانة مفتوحة"| EL["8 قواعد أهلية<br/>أول فشل يُسجَّل"]
    EL --> RK["ترتيب من 100<br/>+ مقارنة بالأقرب"]
    RK --> SUP["اعتماد المشرف<br/>(إعادة فحص في معاملة)"]
    SUP --> EMP["رد الموظف<br/>(رابط موقّع)"]
    EMP --> COV["الخانة مغطاة"]
```

## التطبيقان واتجاه الاعتماد

```mermaid
flowchart LR
    C["coverage<br/>المواقع · المتطلبات · المناوبات<br/>التكليفات · لقطات المحرك"] -->|يستورد| W["workforce<br/>الموظفون · المهارات · المناطق<br/>قواعد الفئات · الغياب"]
```

- `coverage` يعرف `workforce`، والعكس **ممنوع** (`test_workforce_never_imports_coverage`).
- لهذا تعيش بيانات المرجع المشتركة (`Zone`، `Skill`) في `workforce`.

## الطبقات داخل `coverage`

| الطبقة | الملف | الحالة | تفعل | لا تفعل |
|---|---|---|---|---|
| الرموز | `codes.py` | منفذ | قيم ثابتة يتشاركها الكل | — |
| السياسة | `policy.py` | منفذ | كل رقم تشغيلي مع سببه | منطق |
| النماذج | `models.py` | منفذ | البيانات والقيود البنيوية | مقارنة أشخاص بمواقع |
| المستورد | `importer.py` | منفذ | قراءة الحزمة والتحقق والتحميل الذري | قواعد المحرك |
| الخدمات | `services/` | منفذ | كل قاعدة تشغيلية؛ الحساب نقي بلا ORM | قراءة `request` |
| الواجهات | `views.py`، `api/` | منفذ | استقبال، تحقق، صلاحية، استدعاء الخدمة | قواعد أعمال |
| استيراد التنبؤ | `forecast_import.py` | منفذ (P0-3) | فحص ملف التنبؤ وكتابته ذريًا | حساب أي تنبؤ |
| الأوامر | `management/commands/` | `load_demo` (مع `--scenario`)، و`import_forecast`، و`create_supervisor`، و`session_report` | تشغيل الخدمات من الطرفية | تكرار منطق |

## لماذا منطق الأعمال في Services لا في Views ولا في Models

- **في View:** القاعدة نفسها ستلزم في الـAPI، وفي صفحة HTML، وفي الاختبار، وفي إعادة الفحص عند الاعتماد. أربع نسخ تنحرف عن بعضها.
- **في Model:** النموذج يعرف صفه فقط. أما «هل يمكن نقل نوره من S12 دون كسر حدها؟» فتحتاج موقعين ومناوبة وكل تكليفاتهما.
- **في Services:** دالة واحدة يستدعيها الجميع. وإذا كانت نقية (لقطة ← نتيجة) تُختبر بلا قاعدة بيانات.

## كيف يتفاعل التنبؤ مع المحرك

باتجاه واحد، وعبر ملف:
- `forecast/` لا يستورد Django، وDjango لا يستورد `forecast/`. الجسر ملف JSON بصيغة `covercast-forecast-v1`.
- `import_forecast` يتحقق من الملف ويكتبه `ForecastRun` مع أيامه، في معاملة واحدة.
- `snapshot.forecast_for` يقرأ مستوى اليوم من التشغيل **الفعّال** فقط. ثم `build_demand` يحوّل المستوى إلى `volume_uplift` عبر `DemandRule`.
- بلا تشغيل فعّال لليوم: `volume_uplift = 0`، ويُحتسب عدد اليوم الوطني إن وُجد (BR-02).
- إعادة عرض أوساكا لا تؤثر في أي خانة، لأن 2025 بلا مناوبات.
- `services/forecast_view.day_forecast` يقرأ الشيء نفسه للوحة و`GET /api/forecast`، ويحسب الأثر من جدول `DemandRule` نفسه، فلا تعرض اللوحة أثرًا لا يطبّقه المحرك.
