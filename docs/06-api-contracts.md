# 06 — عقود الـAPI

**الحالة:** معتمدة ضمن D3 (`phase-0-audit.md` §11.2). **منفذة في المرحلة 8أ** (`coverage/api/`). أسماء الحقول مثبّتة باختبار `ApiContractTests`: أي إعادة تسمية تُفشله.

**ملاحظة صادقة:** نص العقد الأصلي في التقرير §9 لم يكن متاحًا في المستودع ولا في الملفات المرفوعة. بُني العقد على جدول التعديلات المعتمد أدناه، وهذا الملف هو المرجع من الآن.

## النقاط

| الطريقة | المسار | الغرض | الحماية |
|---|---|---|---|
| GET | `/api/board?date=2030-10-14` | كل المواقع في مناوبتي اليوم | مشرف |
| GET | `/api/forecast?date=2030-10-24` | مستوى ضغط الزوار لليوم، ومصدره، وأثره على الاحتياج. **أُضيف في P0-6 (15 أكتوبر 2026)** | مشرف |
| GET | `/api/requirements/{id}/candidates?slot=1` | المرشحون والمستبعدون والمقارنة | مشرف |
| POST | `/api/requirements/{id}/approve` | `{"employee_code", "idempotency_key", "slot"?}` | مشرف + CSRF |
| POST | `/api/assignments/{id}/absence` | `{"kind": "leave" \| "sick" \| "no_show"}` | مشرف + CSRF |
| GET/POST | `/offer/{signed_token}` | قبول الموظف أو اعتذاره (صفحة HTML) | رابط موقّع، بلا دخول |
| GET | `/board/live?date=…&v=…` | تحديث اللوحة التلقائي: **204** إن لم يتغير شيء (فحص قراءة فقط، بلا كتابة)، أو **200** بجزء HTML من اللوحة وترويسة `X-Board-Version`. ليس JSON، فهو جزء الصفحة نفسه | مشرف. بلا دخول أو لغير المشرف: **403** (لا تحويل لصفحة الدخول) |

## الحماية

- **الدخول:** جلسة Django. المشرف عضو في مجموعة `supervisors` (`policy.SUPERVISOR_GROUP`)، أو superuser.
  - بلا دخول: 403 `AUTH_REQUIRED`.
  - بدخول، لكن ليس مشرفًا: 403 `NOT_SUPERVISOR`.
- **CSRF** على كل POST: الواجهة ترسل ترويسة `X-CSRFToken` بقيمة الكوكي `csrftoken`. بدونها: 403 `CSRF_FAILED` بصيغة JSON (`settings.CSRF_FAILURE_VIEW`).
- طريقة غير مسموحة: 405.
- العربية تُرسل نصًا لا رموز `\u`. الأوقات بتوقيت الرياض (`+03:00`).

## 1. اللوحة

```text
{ date, summary: {cells, open, confirmed, anticipated},
  shifts: [ { date, slot, label_ar, starts_at, ends_at,
              sites: [ { ...requirement, zone, importance, site_type,
                         assignments: [ { assignment_id, employee_code, name, status, origin,
                                          absent, awaiting_response } ] } ] } ] }
```

- `absent`: مكلّف، لكن عليه غياب يتقاطع مع المناوبة، فلا يُعدّ حاضرًا (مثل E019 في G2).
- `awaiting_response`: بديل معتمد لم يرد بعد.
- التكليفات الملغاة لا تظهر.
- اللوحة **تحدّث لقطة `CoverageRequirement`** لكل خانة، حتى يكون لكل خانة `id` ثابت (A-24).
- يوم الاثنين 14 أكتوبر 2030: `summary = {cells: 24, open: 1, confirmed: 0, anticipated: 1}`، أي G1 وحدها.

## 1أ. ضغط الزوار (P0-6)

`GET /api/forecast?date=YYYY-MM-DD`. نقطة جديدة؛ **لم يتغير أي حقل في `/api/board`**. والقاموس نفسه تبنى منه لوحة «ضغط الزوار».

```text
{ date, rules_loaded,
  forecast: null | {
    level, level_ar, mode, mode_ar, data_origin, provenance_ar,
    run: { code, model, level_method, note, freeze_sha256 },
    visitors: null | { p10, p50, p90, actual, actual_level, as_of, horizon_days,
                       threshold_high, threshold_peak },
    staffing_effect: { applies_to_shifts, total_added,
                       by_category: [ { category, category_ar, site_types, sites,
                                        extra_staff_per_site } ] } } }
```

- `forecast: null`: لا تشغيل فعّال يغطي اليوم (مثل 14 أكتوبر 2030).
- `mode`: `osaka_replay` (رسمي، 2025) أو `riyadh_scenario` (محاكى، 2030).
- `visitors: null` في السيناريو دائمًا. المستوى فيه افتراض، ولا يوجد رقم زوار.
- `applies_to_shifts: false` في أيام أوساكا: لا مناوبات، والأثر «للتوضيح فقط».
- `staffing_effect` من جدول `DemandRule` نفسه الذي يطبّقه المحرك.
- **أمثلة محققة باختبار `ForecastApiTests`:**
  - 24 أكتوبر 2030: `level = peak`، و`total_added = 13`.
  - 13 سبتمبر 2025: `p50 = 209837`، و`actual = 218130`، و`actual_level = peak`.
- **أخطاء:** تاريخ غير مقروء ← 400 `INVALID_INPUT`. والحماية كبقية النقاط.

## 2. المرشحون

يشغّل `evaluate_slot` ويحفظ 80 صف تدقيق بـ`run_id` واحد. إن كان الموقع مغطى: 409 `GAP_ALREADY_FILLED`.

- `requirements_met`: كم شرطًا من شروط الخانة يحققه («3/3»). المؤهل يحققها كلها دائمًا، لأن الشرط صارم (R1).
- `excluded_summary`: العدد لكل قاعدة، **بترتيب الفحص** لا بالعدد.
- `reason_ar`: من قوالب الفريق، والمهارات بأسمائها العربية. الرموز تبقى في `detail`.
- `baseline.violates`: أول قاعدة يخالفها اختيار «الأقرب»، أو `null`.

**استجابة G1 الحقيقية** (مختصرة: مرشح واحد من اثنين، ومستبعد واحد من 78):

```json
{
  "requirement": {
    "id": 3,
    "site_code": "S03",
    "site_name": "نقطة الإسعاف 3 - بجوار الجناح الياباني",
    "shift": {
      "date": "2030-10-14",
      "slot": "morning",
      "label_ar": "صباحية",
      "starts_at": "2030-10-14T07:00:00+03:00",
      "ends_at": "2030-10-14T15:00:00+03:00"
    },
    "base": 2,
    "event_uplift": 0,
    "volume_uplift": 0,
    "required_total": 2,
    "present": 2,
    "headcount_gap": 0,
    "gap_nature": "anticipated",
    "open_slots": [
      {
        "slot": 1,
        "must_have": [
          "adv_first_aid",
          "ja",
          "paramedic"
        ],
        "must_have_ar": [
          "إسعاف أولي متقدم",
          "اليابانية",
          "مسعف"
        ],
        "source": "event:1"
      }
    ],
    "drivers": [
      {
        "kind": "base",
        "label": "الحد الأدنى الدائم للموقع",
        "adds": 2,
        "skills": []
      },
      {
        "kind": "event",
        "label": "اليوم الوطني لليابان - حفل الاحتفال",
        "adds": 0,
        "skills": [
          "adv_first_aid",
          "ja",
          "paramedic"
        ]
      }
    ]
  },
  "slot": {
    "slot": 1,
    "must_have": [
      "adv_first_aid",
      "ja",
      "paramedic"
    ],
    "must_have_ar": [
      "إسعاف أولي متقدم",
      "اليابانية",
      "مسعف"
    ],
    "source": "event:1"
  },
  "run_id": "…",
  "candidates": [
    {
      "rank": 1,
      "employee_code": "E061",
      "name": "نوره القحطاني",
      "category": "loan",
      "tag": "recommended",
      "score": 60.83,
      "score_parts": {
        "proximity": 22.5,
        "hours_headroom": 8.33,
        "extra_skills": 20.0,
        "donor_safety": 0.0,
        "fairness": 10.0
      },
      "requirements_met": "3/3",
      "travel": 10,
      "hours_after": 32,
      "hours_cap": 48,
      "donor_site": "S12",
      "reasons": [
        {
          "code": "REQ_MET",
          "kind": "positive",
          "text_ar": "يستوفي 3 من 3 متطلبات الموقع"
        },
        {
          "code": "NEAR",
          "kind": "positive",
          "text_ar": "الأقرب: على بعد 10 دقائق من الموقع"
        },
        {
          "code": "KEEPS_MIN_AT_SITE",
          "kind": "positive",
          "text_ar": "نقله لا يكسر الحد الأدنى في موقعه الحالي (S12)"
        },
        {
          "code": "HOURS_OK",
          "kind": "positive",
          "text_ar": "ساعاته بعد التكليف 32 من 48"
        }
      ]
    }
  ],
  "excluded": [
    {
      "employee_code": "E060",
      "name": "ريم العنزي",
      "category": "volunteer",
      "failed_rule": "CERT_EXPIRED",
      "reason_ar": "شهادة إسعاف أولي متقدم تنتهي في 2030-10-13، قبل موعد الوردية",
      "detail": {
        "skill": "adv_first_aid",
        "date": "2030-10-13",
        "skills": [
          "adv_first_aid"
        ],
        "shift_date": "2030-10-14"
      }
    }
  ],
  "excluded_summary": {
    "ALREADY_ASSIGNED": 2,
    "MISSING_SKILL": 74,
    "CERT_EXPIRED": 1,
    "SCOPE_OUT": 1
  },
  "baseline": {
    "employee": "E053",
    "name": "احمد الدوسري",
    "travel": 10,
    "violates": "SCOPE_OUT",
    "violates_ar": "خارج نطاق الإعارة",
    "breaks_site": "S05",
    "donor_short_by": 1
  }
}
```

## 3. الاعتماد

- `201` اعتماد جديد. `200` إعادة للطلب نفسه (`replayed: true`)، ولا يُنشأ شيء.
- `moved_from_site`: الموقع الذي نُقل منه الموظف في المناوبة نفسها، أو `null` إن كان استدعاءً (ADR 0008).
- `offer_url`: رابط الموظف. **لا يُرسل آليًا** (لا بريد ولا SMS في الـMVP)؛ يظهر للمشرف (A-25).

```json
{
  "assignment_id": 195,
  "employee_code": "E061",
  "status": "planned",
  "replayed": false,
  "moved_from_site": "S12",
  "offer_url": "http://testserver/offer/<signed-token>"
}
```

## 4. الغياب

`201` غياب جديد، أو `200` إن كان مسجّلًا من قبل (`created: false`). الاستجابة: `{absence_id, created, kind, requirement}`، و`requirement` بعد التسجيل مباشرة.

مثال: غياب المسعف E018 صباح 14 أكتوبر يحوّل G1 من `anticipated` إلى `confirmed`.

## التغييرات المعتمدة عن عقد التقرير §9

**لا إعادة تسمية لأي حقل قائم.**

| الحقل | التقرير | المعتمد |
|---|---|---|
| `requirement.gap_nature` | `"forecasted"` | `"confirmed"` \| `"anticipated"` |
| `requirement.open_slots` | — | `[{"slot": 1, "must_have": ["paramedic", "adv_first_aid", "ja"]}]` |
| `candidates[].tag` | — | `"recommended"` \| `"backup"` \| `null` |
| `candidates[].requirements_met` | — | `"3/3"` |
| `excluded[].failed_rule` | مفتوح | قيم `ExclusionCode` الثمانية (`coverage/codes.py`)، بترتيب الفحص: ABSENT أولًا |
| `baseline` | — | `{"employee", "travel", "violates", "breaks_site", "donor_short_by"}`. حقل `violates` أُضيف في المرحلة 5: أول قاعدة يخالفها اختيار «الأقرب» |
| `score_parts` | `unassigned` | `donor_safety` |
| `open_slots[].must_have_ar` | — | أسماء المهارات بالعربية بجانب الرموز. أُضيف في المرحلة 8أ |
| `shift.starts_at` | — | بتوقيت الرياض `+03:00`. أُضيف في المرحلة 8أ |
| `requirement.drivers[].kind` | — | `base` \| `event` \| `forecast` \| `ignored` \| **`fallback`** (أُضيف 15 أكتوبر 2026: عدد يوم وطني احتُسب لأن لا تنبؤ يغطي اليوم، BR-02). ونص سبب `forecast` يذكر المصدر: «افتراض سيناريو محاكى، ليس تنبؤًا» للسيناريو |
| `GET /api/forecast` | — | نقطة جديدة (P0-6)، القسم 1أ |

**أخطاء موحّدة:** `{"error": {"code": "...", "message_ar": "...", "details": {...}}}`، مع 400 / 403 / 404 / 409.

**الرموز منفذة منذ المرحلة 6** (`coverage/codes.py: ErrorCode`، والحالة HTTP في `services/errors.py`):

| الرمز | HTTP | متى |
|---|---|---|
| `INVALID_INPUT` | 400 | مفتاح طلب فارغ أو أطول من 64، نوع غياب غير معروف، JSON غير صالح، تاريخ غير مقروء، `slot` ليس رقمًا |
| `OFFER_INVALID` | 403 | رابط مزوّر أو معدّل، أو لتكليف ليس بديلًا |
| `AUTH_REQUIRED` / `NOT_SUPERVISOR` / `CSRF_FAILED` | 403 | المرحلة 8أ: انظري «الحماية» |
| `EMPLOYEE_NOT_FOUND` | 404 | |
| `NOT_FOUND` | 404 | رقم خانة أو تكليف غير موجود (`details.what`) |
| `GAP_ALREADY_FILLED` | 409 | سُدّت الخانة قبل هذا الطلب |
| `CANDIDATE_NOT_ELIGIBLE` | 409 | `details.failed_rule` أحد رموز `ExclusionCode` الثمانية |
| `IDEMPOTENCY_KEY_REUSED` | 409 | المفتاح نفسه لطلب مختلف |
| `SHIFT_ENDED` | 409 | |
| `ASSIGNMENT_NOT_ACTIVE` | 409 | تسجيل غياب على تكليف ملغى |
| `OFFER_EXPIRED` | 409 | مضت 24 ساعة، أو انتهت المناوبة |
| `OFFER_CLOSED` | 409 | رد معاكس بعد الرد الأول، أو عرض مسحوب |
