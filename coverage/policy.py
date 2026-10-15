"""Business-rule constants for CoverCast.

Every number the engine uses lives here, with the reason it has this value.
Values marked ASSUMPTION have no official source; they are listed in
docs/12-assumptions.md and can be changed here only (never inline in code).
Kept in code, not in the database, so a change goes through review and tests.
"""

# Length of one shift in hours (07-15 and 15-23 in the demo pack).
SHIFT_HOURS = 8

# ASSUMPTION A-03: the working week for the hours cap runs Sunday-Saturday.
# Python's date.weekday(): Monday=0 ... Sunday=6.
WEEK_START_WEEKDAY = 6

# ASSUMPTION A-05: someone with no assignment in the target shift is called in
# from outside; we do not know where they are, so we use a fixed call-in time.
OFF_DUTY_CALL_IN_MINUTES = 60

# Event kinds whose effect on visitor numbers a forecast already captures.
# Their EventImpact rows must not add headcount (they add skills only),
# otherwise the same crowd would be counted twice: once by the forecast,
# once by the event.
FORECAST_COVERED_EVENT_KINDS = frozenset({"national_day", "holiday"})
# ...but only WHEN a forecast covers the day (decision approved by Nouf on
# 2026-10-15, docs/18 section 3.1). With no forecast for the day, the event's
# headcount is the only estimate of its crowd, so it is counted. The two cases
# exclude each other, so the crowd is never counted twice.

# Volume uplift categories (simulated MVP assumption approved by Nouf on
# 2026-10-15; NOT an official staffing standard). Every site type in the data
# must map to exactly one category; the loader refuses a DemandRule file that
# disagrees with this mapping or leaves a site type/level out.
VOLUME_CATEGORY_BY_SITE_TYPE = {
    "security_gate": "gates_security",
    "crowd": "crowd_management",
    "visitor_service": "visitor_services",
    "medical": "medical",
    "operations": "operations_safety",
    "safety": "operations_safety",
}
VOLUME_CATEGORY_AR = {
    "gates_security": "البوابات والأمن",
    "crowd_management": "إدارة الحشود",
    "visitor_services": "خدمة الزوار والإرشاد",
    "medical": "نقاط الإسعاف",
    "operations_safety": "العمليات والسلامة",
}

# A Riyadh scenario run must use dates inside the Expo 2030 period.
RIYADH_SCENARIO_DATES = ("2030-10-01", "2031-03-31")
# An Osaka replay run must use dates of Expo 2025 (official series).
OSAKA_REPLAY_DATES = ("2025-04-13", "2025-10-13")
FORECAST_MAX_HORIZON_DAYS = 7

# Ranking weights (report section 6; decision D3). They sum to 100.
# These are proposed weights, not a probability of success.
WEIGHTS = {
    "proximity": 30,
    "hours_headroom": 25,
    "extra_skills": 20,
    "donor_safety": 15,
    "fairness": 10,
}

# Proximity is scaled between the nearest and farthest zone travel time.
PROXIMITY_BEST_MINUTES = 5
PROXIMITY_WORST_MINUTES = 25

# Fairness: points lost per replacement shift this person already has in the
# same working week (Sunday-Saturday). Counted from the schedule, not from
# "today", so the same data always gives the same ranking.
FAIRNESS_PENALTY_PER_RECENT_REPLACEMENT = 5

# A headcount-only slot (site below base_staff, every skill line still met):
# should the replacement also hold the site's base skills? YES (decision
# A-15, approved by Nouf on 2026-10-13). The replacement must be able to do
# the site's work, not just fill a chair. Example: S10 needs 3 people and at
# least one crowd_control holder; after E019's no-show the missing third
# person must also hold crowd_control (as gap_definitions.csv says for G2),
# so a paramedic is never pulled into a shuttle station.
GENERIC_SLOT_REQUIRES_BASE_SKILLS = True

# ASSUMPTION A-04 (from Raghad's pack): within this many hours of the cap, an
# eligible candidate is tagged "backup" rather than "recommended".
SOFT_HOURS_MARGIN = 2

# Phase 6: a signed offer link stays valid this long (it also stops working
# once the shift has ended, whichever comes first).
OFFER_TOKEN_MAX_AGE_HOURS = 24
OFFER_TOKEN_SALT = "covercast.offer.v1"

# Phase 8: who may see candidates, approve and report absences.
# Members of this Django group (or superusers). Created by
# `manage.py create_supervisor`.
SUPERVISOR_GROUP = "supervisors"
