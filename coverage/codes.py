"""Fixed codes shared by models, services, API and tests.

Changing a value here changes the API contract with the UI (docs/06-api-contracts.md).
"""

from django.db import models

from workforce.codes import DataOrigin  # noqa: F401  (re-exported for coverage modules)


class ShiftSlot(models.TextChoices):
    MORNING = "morning", "صباحية"
    EVENING = "evening", "مسائية"


class AssignmentStatus(models.TextChoices):
    PLANNED = "planned", "مخطط"
    CONFIRMED = "confirmed", "مؤكد"
    ABSENT = "absent", "غائب"
    CANCELLED = "cancelled", "ملغى"


# Statuses that count as "someone is covering this slot".
ACTIVE_ASSIGNMENT_STATUSES = (AssignmentStatus.PLANNED, AssignmentStatus.CONFIRMED)


class AssignmentOrigin(models.TextChoices):
    SCHEDULE = "schedule", "من الجدول"
    REPLACEMENT = "replacement", "بديل معتمد"


class EventKind(models.TextChoices):
    NATIONAL_DAY = "national_day", "يوم وطني"
    DELEGATION = "delegation", "وفد رسمي"
    VIP_VISIT = "vip_visit", "زيارة كبار شخصيات"
    SPECIAL = "special", "فعالية خاصة"
    HOLIDAY = "holiday", "عطلة"


class AttendanceLevel(models.TextChoices):
    NORMAL = "normal", "عادي"
    HIGH = "high", "مرتفع"
    PEAK = "peak", "ذروة"


class ForecastMode(models.TextChoices):
    """The two kinds of forecast run (docs/18 section 3.2). They never mix."""

    OSAKA_REPLAY = "osaka_replay", "إعادة عرض أوساكا 2025 (بيانات رسمية)"
    RIYADH_SCENARIO = "riyadh_scenario", "سيناريو الرياض 2030 (محاكاة)"


class ExclusionCode(models.TextChoices):
    """First failed rule, in the order the eligibility filter checks them."""

    ABSENT = "ABSENT", "غائب أو في إجازة"
    ALREADY_ASSIGNED = "ALREADY_ASSIGNED", "مكلّف بالفعل في هذا الموقع"
    MISSING_SKILL = "MISSING_SKILL", "لا يحمل المهارة المطلوبة"
    CERT_EXPIRED = "CERT_EXPIRED", "شهادة منتهية في تاريخ المناوبة"
    SCOPE_OUT = "SCOPE_OUT", "خارج نطاق الإعارة"
    HOURS_CAP = "HOURS_CAP", "يتجاوز سقف الساعات"
    REST_GAP = "REST_GAP", "فاصل الراحة غير كافٍ"
    BREAKS_MINIMUM = "BREAKS_MINIMUM", "سحبه يكسر حد موقعه"


class ErrorCode(models.TextChoices):
    """Errors the approval workflow can return (docs/06-api-contracts.md).

    The label is the Arabic message shown to the supervisor or employee.
    The HTTP status for each code lives in coverage/services/errors.py.
    """

    GAP_ALREADY_FILLED = "GAP_ALREADY_FILLED", "سُدّت هذه الفجوة بالفعل"
    CANDIDATE_NOT_ELIGIBLE = "CANDIDATE_NOT_ELIGIBLE", "المرشح لم يعد مؤهلًا لهذه الخانة"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED", "مفتاح الطلب استُخدم لطلب مختلف"
    EMPLOYEE_NOT_FOUND = "EMPLOYEE_NOT_FOUND", "الموظف غير موجود أو غير نشط"
    SHIFT_ENDED = "SHIFT_ENDED", "انتهت المناوبة"
    ASSIGNMENT_NOT_ACTIVE = "ASSIGNMENT_NOT_ACTIVE", "التكليف غير نشط"
    OFFER_INVALID = "OFFER_INVALID", "الرابط غير صالح"
    OFFER_EXPIRED = "OFFER_EXPIRED", "انتهت صلاحية الرابط"
    OFFER_CLOSED = "OFFER_CLOSED", "تم الرد على هذا العرض أو سُحب"
    INVALID_INPUT = "INVALID_INPUT", "مدخلات غير صحيحة"
    # Phase 8: the API layer
    AUTH_REQUIRED = "AUTH_REQUIRED", "يلزم تسجيل الدخول"
    NOT_SUPERVISOR = "NOT_SUPERVISOR", "هذه العملية للمشرفين فقط"
    CSRF_FAILED = "CSRF_FAILED", "انتهت صلاحية الصفحة، يرجى إعادة تحميلها"
    NOT_FOUND = "NOT_FOUND", "العنصر المطلوب غير موجود"
