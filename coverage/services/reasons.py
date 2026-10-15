"""Reason texts shown to the supervisor, keyed by fixed codes.

Copied from the team's data/demo/v0.2/reason_templates.csv; a test fails if
the two ever differ. The UI renders these templates; no language model is
used to word reasons (decision D3: templates only, no invented text).
"""
from __future__ import annotations

# code -> (kind, Arabic template, English template)
TEMPLATES: dict[str, tuple[str, str, str]] = {
    'REQ_MET': ('positive', 'يستوفي {met} من {total} متطلبات الموقع', 'Meets {met} of {total} site requirements'),
    'KEEPS_MIN_AT_SITE': ('positive', 'نقله لا يكسر الحد الأدنى في موقعه الحالي ({site})', 'Moving them keeps {site} at its minimum'),
    'NEAR': ('positive', 'الأقرب: على بعد {minutes_ar} من الموقع', 'Only {minutes} min away'),
    'HOURS_OK': ('positive', 'ساعاته بعد التكليف {hours_after} من {cap}', '{hours_after} of {cap} weekly hours after assignment'),
    'NEAR_HOURS_CAP': ('caution', 'قريب من حد الساعات: {hours_after} من {cap}', 'Close to the hours cap: {hours_after} of {cap}'),
    'CERT_EXPIRED': ('exclusion', 'شهادة {skill} تنتهي في {date}، قبل موعد الوردية', 'Certificate {skill} expires on {date}, before the shift'),
    'HOURS_CAP': ('exclusion', 'سيتجاوز حد الساعات الأسبوعي ({hours_after} من {cap})', 'Would exceed the weekly hours cap'),
    'SCOPE_OUT': ('exclusion', 'نطاق إعارته لا يشمل المنطقة {zone}', 'Secondment scope does not include zone {zone}'),
    'ABSENT': ('exclusion', 'غائب أو في إجازة خلال الوردية', 'Absent or on leave during the shift'),
    'ALREADY_ASSIGNED': ('exclusion', 'مكلّف بالفعل في هذا الموقع', 'Already assigned to this site'),
    'REST_GAP': ('exclusion', 'فاصل الراحة أقل من {rest_ar}', 'Rest gap shorter than {rest} h'),
    'BREAKS_MINIMUM': ('exclusion', 'نقله يكسر الحد الأدنى أو متطلبات موقعه الحالي ({site})', 'Moving them would break the minimum at {site}'),
    'MISSING_SKILL': ('exclusion', 'لا يحمل المهارة المطلوبة: {skill}', 'Does not hold the required skill: {skill}'),
    'TRAVEL': ('neutral', 'على بعد {minutes_ar} من الموقع', '{minutes} min away'),
}


def counted(n: int, one: str, two: str, few: str, many: str) -> str:
    """Arabic number agreement: 1 دقيقة واحدة، 2 دقيقتان، 3-10 دقائق، 11+ دقيقة."""
    if n == 1:
        return one
    if n == 2:
        return two
    return f"{n} {few if 3 <= n % 100 <= 10 else many}"


def minutes_phrase(n: int) -> str:
    return counted(n, "دقيقة واحدة", "دقيقتين", "دقائق", "دقيقة")


def hours_phrase(n: int) -> str:
    return counted(n, "ساعة واحدة", "ساعتين", "ساعات", "ساعة")


def render(code: str, params: dict, lang: str = "ar") -> str:
    """Fill a template. Missing parameters stay visible as {name} instead of crashing.

    Arabic templates use ready-made count phrases ({minutes_ar}, {rest_ar}),
    built here from the plain numbers, so "10 دقائق" and "25 دقيقة" are both right.
    """
    kind, ar, en = TEMPLATES[code]
    text = ar if lang == "ar" else en
    params = dict(params)
    if isinstance(params.get("minutes"), int):
        params.setdefault("minutes_ar", minutes_phrase(params["minutes"]))
    if isinstance(params.get("rest"), int):
        params.setdefault("rest_ar", hours_phrase(params["rest"]))

    class _Keep(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    flat = {k: (", ".join(map(str, v)) if isinstance(v, (list, tuple)) else v) for k, v in params.items()}
    return text.format_map(_Keep(flat))
