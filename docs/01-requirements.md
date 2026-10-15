# 01 — المتطلبات وتتبعها

المصدر: التقرير §8 (FR-1…FR-10، NFR-1…NFR-6)، وتحديث 13 أكتوبر (R1–R7)، والقرارات D1–D6، ثم ADR 0010 وموافقة نوف على P0 (15 أكتوبر 2026): FR-11…FR-13 وNFR-7…NFR-9. الحالة محدّثة حتى P0.

**كيف يُقرأ الجدول:**
- **المتطلب الوظيفي (FR):** شيء يفعله النظام للمستخدم.
- **غير الوظيفي (NFR):** صفة يجب أن يتصف بها، مثل الأداء أو الشفافية.
- كل سطر يشير إلى الكود الذي ينفذه والاختبار الذي يثبته.

| المتطلب | التنفيذ | الاختبار | الحالة |
|---|---|---|---|
| FR-1 استيراد بأمر واحد بنتيجة ثابتة | `load_demo`، `importer.py` | `IdempotencyTests` (4)، `RejectionTests` (8) | **منفذ** |
| FR-2 الاحتياج بمكوناته | `build_demand`، `refresh_requirement` | `BuildDemandTests`، `test_refresh_requirement_keeps_one_row_per_site_and_shift` | **منفذ** |
| FR-3 لوحة الغد | `services/board.day_board`، صفحة `/board`، و`/api/board` | `BoardPageTests`، `BoardTests` | **منفذ** |
| FR-4 تسجيل غياب | `services/absence.report_absence`، زر في اللوحة، و`/api/assignments/{id}/absence` | `AbsenceTests` (×2)، اختبار المتصفح | **منفذ** |
| FR-5 الاستبعاد بالقيود | `filter_eligible` | `RuleTests` (16)، `test_totals_match_the_hand_derivation` | **منفذ** |
| FR-6 الترتيب مع التفصيل | `rank` + `CandidateEvaluation` | `RankingTests` (10)، `test_every_hand_derived_row_matches` | **منفذ** |
| FR-7 اعتماد المشرف | `services/approval.approve` + CHECK في قاعدة البيانات | `test_approval`، `test_api`، `test_concurrency` | **منفذ** |
| FR-8 إعادة الفحص عند الاعتماد | `approve` يعيد `gap_for` و`evaluate` داخل معاملة IMMEDIATE | `test_certificate_changed_after_viewing_is_caught`، `test_concurrency` | **منفذ** |
| FR-9 قبول الموظف | `services/offers.respond`، صفحة `/offer/<token>` الموقّعة | `OfferTests`، `OfferPageTests` | **منفذ** |
| FR-10 شريط التوقع | لوحة «ضغط الزوار» (`_forecast_panel.html`) فوق لوحة اليوم | `ForecastPanelPages` (4)، اختبار المتصفح | **منفذ (P0-6)**. كان «المحرك جاهز، ولا أمر استيراد» حتى 15 أكتوبر 2026 |
| FR-11 استيراد ملف تنبؤ بفحص كامل | `forecast_import.py`، `import_forecast` | `test_forecast_import` (26) | **منفذ (P0-3)** |
| FR-12 المستوى يغيّر الاحتياج عبر `DemandRule` بلا احتساب مزدوج | `snapshot.forecast_for`، `build_demand` | `BuildDemandTests` (الحدث مع التنبؤ وبدونه)، `test_forecast_engine` | **منفذ (P0-4)** |
| FR-13 سيناريو رياض محاكى منفصل، وإعادة عرض أوساكا الرسمية | `load_demo --scenario riyadh_peak`، `export_forecast.py` | `PeakDay`، `G1G2Unchanged`، `test_f5_export` | **منفذ (P0-5)** |
| NFR-1 الشفافية | `data_origin`، وشارة «بيانات محاكاة» في كل صفحة (`context_processors.demo_data`) | `test_everything_is_marked_simulated`، `SimulatedDataIsAlwaysDisclosed` | **منفذ** |
| NFR-2 الحتمية | لا عشوائية؛ بصمة الاستيراد؛ فك تعادل ثابت | `test_order_is_identical_over_100_shuffles` | **منفذ** |
| NFR-3 الأداء < 1 ثانية | استعلامات مجمّعة في `candidate_facts`؛ اللوحة لا تحسب الاحتياج مرتين | `test_query_count_does_not_grow_with_people`، `test_query_count_is_bounded` | **منفذ**: المرشحون 0.08–0.10 ثانية، واللوحة 0.11 |
| NFR-4 بلا إنترنت | الخط والـCSS والـJS محلية | `test_no_external_address_in_templates_css_or_js`، واختبار المتصفح (صفر طلبات خارجية) | **منفذ** |
| NFR-5 الاختبارات | 305 اختبارات Django، و121 اختبار تنبؤ (2 متخطاة بلا chronos) | — | **منفذ** (15 أكتوبر 2026) |
| NFR-6 العربية RTL | `dir="rtl"`، وخط IBM Plex Sans Arabic، والأعداد العربية متطابقة | `test_pages` | **منفذ** |
| NFR-7 فصل الرسمي عن المحاكى | وضعان (`osaka_replay`/`riyadh_scenario`) بقيد في قاعدة البيانات، والمصدر مكتوب بالكلمات في كل لوحة، وتذييل أيام أوساكا يقول إن أرقام الزوار رسمية | `test_mode_must_match_data_origin`، `test_osaka_day_is_official_and_not_riyadh` | **منفذ** |
| NFR-8 لا تسرب في التنبؤ | `as_of` وأفق لكل يوم، والحدود من البيانات حتى `as_of` | `NoLookAhead`، `test_no_look_ahead` | **منفذ** |
| NFR-9 لا نموذج تعلم آلة داخل مسار طلبات Django | النماذج في `forecast/`، وDjango يقرأ ملفًا | ADR 0009؛ لا استيراد من `forecast/` في `coverage/` | **منفذ** |
| R1 شرط مقابل ميزة + `MISSING_SKILL` | الشرط قاعدة صارمة | 74 `MISSING_SKILL` في G1 | **منفذ** |
| R2 لا عربية في G1 | `build_v02.fix_gap_definitions` | فحص `--check` | **منفذ** |
| R3 الفجوة يستنتجها المحرك | `detect_gaps`؛ الملف للمقارنة فقط | `test_exactly_the_two_planted_gaps_in_the_whole_window` | **منفذ** |
| R4 bundle | عمود + معنى موثق | اختبار bundle، ورفض أعداد مختلفة | **منفذ** |
| R5 زمن الاستدعاء | `policy.OFF_DUTY_CALL_IN_MINUTES` | خالد: 60 دقيقة ← 0 نقاط قرب | **منفذ** |
| R6 الأهمية ونوع الموقع | `Site.importance`، `site_type` | `SiteTests` | **منفذ** |
| R7 تواريخ 2030 | `DATE_SHIFT = 1463` | `test_demo_window_is_in_expo_2030` | **منفذ** |
