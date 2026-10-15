#!/usr/bin/env python3
"""
CoverCast — synthetic data generator (schema v0.1).

* All data is SIMULATED. Names are synthetic. Fixed seed => reproducible.
* Writes one CSV per table into ./output (UTF-8 with BOM so Excel opens Arabic).
* Plants the demo cases (Noura, Khaled, Reem, Ahmed) inside random background data.
* Engine-output tables (Gap / Candidate / Decision) are NOT generated: the backend
  engine produces them. We only provide gap_definitions.csv + demo_cases.csv
  (expected results) so the engine can be tested.

Run:  python3 generate_covercast_data.py
"""
import csv
import os
import random
from datetime import date, datetime, timedelta, timezone

SEED = 2026
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")

TZ = timezone(timedelta(hours=3))  # Saudi time (UTC+3)
DAYS = [date(2026, 10, 11), date(2026, 10, 12), date(2026, 10, 13)]
EVENT_DAY = DAYS[1]
SHIFTS = {"morning": (7, 15), "evening": (15, 23)}
SHIFT_HOURS = 8
ZONES = list("ABCDE")

CAT_CAP = {"loan": 48, "contractor": 48, "volunteer": 30}   # weekly hours cap (assumption)
REST_MIN = 11                                               # hours (assumption)
SOFT_MARGIN = 2                                             # hours below cap => "backup"


def at(d, h, m=0):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


def iso(dt):
    return dt.isoformat(timespec="minutes")


def shift_window(d, label):
    s, e = SHIFTS[label]
    return at(d, s), at(d, e)


# --------------------------------------------------------------------------- #
# Static catalogues
# --------------------------------------------------------------------------- #
SKILLS = [
    # code, name_ar, name_en, kind
    ("adv_first_aid", "إسعاف أولي متقدم", "Advanced First Aid", "certification"),
    ("basic_first_aid", "إسعاف أولي أساسي", "Basic First Aid", "certification"),
    ("security_license", "ترخيص أمني", "Security License", "certification"),
    ("fire_safety", "السلامة من الحريق", "Fire Safety", "certification"),
    ("crowd_control", "إدارة الحشود", "Crowd Control", "certification"),
    ("paramedic", "مسعف", "Paramedic", "role"),
    ("security_guard", "حارس أمن", "Security Guard", "role"),
    ("visitor_services", "خدمة الزوار", "Visitor Services", "role"),
    ("facility_tech", "فني مرافق", "Facility Technician", "role"),
    ("ar", "العربية", "Arabic", "language"),
    ("en", "الإنجليزية", "English", "language"),
    ("ja", "اليابانية", "Japanese", "language"),
    ("zh", "الصينية", "Chinese", "language"),
    ("fr", "الفرنسية", "French", "language"),
    ("es", "الإسبانية", "Spanish", "language"),
    ("ko", "الكورية", "Korean", "language"),
]
CERT_CODES = {s[0] for s in SKILLS if s[3] == "certification"}
SKILL_NAME_AR = {s[0]: s[1] for s in SKILLS}

# code, name, zone, min_staff, base requirements [(skill, count)]
SITES = [
    ("S01", "نقطة الإسعاف 1", "A", 2, [("adv_first_aid", 1), ("paramedic", 1)]),
    ("S02", "نقطة الإسعاف 2", "B", 2, [("adv_first_aid", 1), ("paramedic", 1)]),
    ("S03", "نقطة الإسعاف 3 - بجوار الجناح الياباني", "C", 2, [("adv_first_aid", 1), ("paramedic", 1)]),
    ("S04", "البوابة الرئيسية", "A", 4, [("security_license", 1), ("crowd_control", 1)]),
    ("S05", "البوابة الشمالية", "B", 3, [("security_license", 1)]),
    ("S06", "نقطة التفتيش الأمني", "A", 3, [("security_license", 2)]),
    ("S07", "مركز العمليات", "D", 2, [("en", 1)]),
    ("S08", "مدخل الوفود الرسمية", "C", 3, [("visitor_services", 1), ("en", 1)]),
    ("S09", "مركز خدمة الزوار", "D", 2, [("visitor_services", 1), ("en", 1)]),
    ("S10", "محطة النقل والمكوكات", "E", 3, [("crowd_control", 1)]),
    ("S11", "مخرج الطوارئ الشرقي", "E", 1, [("fire_safety", 1)]),
    ("S12", "المركز الطبي", "D", 4, [("adv_first_aid", 1), ("paramedic", 1)]),
]
SITE = {s[0]: s for s in SITES}

EVENTS = {
    1: ("national_day", "اليوم الوطني لليابان - حفل الاحتفال", "Japan",
        at(EVENT_DAY, 9), at(EVENT_DAY, 14), "simulated"),
    2: ("delegation", "وفد رسمي ياباني", "Japan",
        at(EVENT_DAY, 11), at(EVENT_DAY, 13), "simulated"),
    3: ("vip_visit", "زيارة كبار الشخصيات", "",
        at(DAYS[2], 9), at(DAYS[2], 11), "simulated"),
    4: ("special", "مؤتمر خاص", "",
        at(DAYS[2], 9), at(DAYS[2], 14), "simulated"),
}
# (event_id, site, skill, count) — requirements added while the event is active
EVENT_REQS = [
    (1, "S03", "ja", 1),
    (2, "S08", "ja", 1),
    (3, "S04", "security_license", 2),
    (4, "S09", "en", 2),
]

# Planted situations -------------------------------------------------------- #
NO_SKIP = None
SKIP_REQS = {("S03", DAYS[1], "morning", "ja")}           # the planted gap: nobody there speaks Japanese
EXTRA_REQS = {("S12", DAYS[1], "morning"): [("paramedic", 2)]}  # keeps Noura's site covered if she moves
FORCE_SIZE = {("S12", DAYS[1], "morning"): 5,             # surplus of 1 at Noura's site (min is 4)
              ("S03", DAYS[1], "morning"): 2,
              ("S10", DAYS[2], "evening"): 3}             # optional absence case
NO_EXTRAS = {("S03", DAYS[1], "morning"), ("S10", DAYS[2], "evening")}

PLANTED = {
    "noura": dict(
        name="نوره القحطاني", category="loan", source="وزارة الصحة", dept="الإسعاف والطوارئ",
        skills={"adv_first_aid": date(2027, 3, 1), "paramedic": None, "basic_first_aid": date(2027, 6, 1),
                "ar": None, "en": None, "ja": None},
        roster={0: "S12", 1: "S12", 2: "S12"}, k_out=1, ot=0, scope={"C", "D", "E"}),
    "khaled": dict(
        name="خالد الشمري", category="contractor", source="شركة الأفق للخدمات التشغيلية", dept="الإسعاف والطوارئ",
        skills={"adv_first_aid": date(2027, 5, 15), "paramedic": None, "ar": None, "en": None, "ja": None},
        roster={0: "S01", 2: "S01"}, k_out=3, ot=0, scope=None),     # off on the event day
    "reem": dict(
        name="ريم العنزي", category="volunteer", source="برنامج المتطوعين", dept="الإسعاف والطوارئ",
        # valid today (11 Oct) but expires BEFORE the event day (12 Oct) -> must be checked on the shift date
        skills={"adv_first_aid": date(2026, 10, 11), "paramedic": None, "ar": None, "en": None, "ja": None},
        roster={0: "S08", 1: "S08", 2: "S08"}, k_out=0, ot=0, scope=None),
    "ahmed": dict(
        name="احمد الدوسري", category="loan", source="وزارة الداخلية", dept="الإسعاف والطوارئ",
        skills={"adv_first_aid": date(2027, 4, 1), "paramedic": None, "ar": None, "en": None, "ja": None},
        roster={0: "S05", 1: "S05", 2: "S05"}, k_out=0, ot=4, scope={"A", "B"}),  # scope excludes zone C
}

MALE = ["عبدالله", "محمد", "فهد", "سعود", "ناصر", "تركي", "سلطان", "بندر", "فيصل", "ماجد", "عمر",
        "ياسر", "طلال", "مشعل", "راكان", "ثامر", "نايف", "صالح", "إبراهيم", "علي", "حمد", "وليد",
        "سامي", "رائد", "زياد", "مازن", "هاني", "بدر", "منصور", "عادل"]
FEMALE = ["لينا", "هند", "العنود", "غادة", "منيرة", "ليلى", "أماني", "دانة", "جود", "رغد", "شهد",
          "لمى", "هيفاء", "عبير", "مها", "أروى", "جواهر", "لطيفة", "وفاء", "نجود", "هدى", "بسمة",
          "ملاك", "تالا", "شروق", "أسماء", "رزان", "سلمى", "مريم", "أريج"]
FAMILY = ["العتيبي", "الغامدي", "الحربي", "الزهراني", "المطيري", "السبيعي", "الشهري", "البقمي", "العمري",
          "الرشيدي", "الهاجري", "الخالدي", "الجهني", "السلمي", "العسيري", "اليامي", "القرني", "الشريف",
          "التميمي", "المالكي"]
BANNED_FIRST = {"نوره", "خالد", "ريم", "ريما", "احمد", "سارة", "يوسف", "نواف"}

PROFILES = [("paramedic", 10), ("medical_other", 3), ("security", 22), ("crowd", 12),
            ("visitor", 16), ("facility", 8), ("ops", 5)]
DEPT = {"paramedic": "الإسعاف والطوارئ", "medical_other": "الإسعاف والطوارئ", "security": "الأمن والسلامة",
        "crowd": "النقل والحشود", "visitor": "خدمة الزوار", "facility": "المرافق والتشغيل", "ops": "العمليات"}
LOAN_SRC = {"paramedic": ["وزارة الصحة", "هيئة الهلال الأحمر"], "medical_other": ["وزارة الصحة"],
            "security": ["وزارة الداخلية"], "crowd": ["وزارة النقل", "وزارة الداخلية"],
            "visitor": ["وزارة السياحة"], "facility": ["وزارة البلديات"], "ops": ["وزارة السياحة", "وزارة النقل"]}
CONTRACTORS = ["شركة الأفق للخدمات التشغيلية", "شركة واحة للتشغيل", "شركة الأمان المتقدم", "شركة رواد الضيافة"]
VOLUNTEER_SRC = ["برنامج المتطوعين", "الجمعيات الأهلية", "جامعة (برنامج الطلاب المتطوعين)"]


class Fail(Exception):
    pass


# --------------------------------------------------------------------------- #
# Employees
# --------------------------------------------------------------------------- #
def cert_expiry(rng, soon_ok=True):
    if soon_ok and rng.random() < 0.08:            # expired / expiring around the window
        return date(2026, 9, 1) + timedelta(days=rng.randint(0, 42))
    return date(2026, 12, 1) + timedelta(days=rng.randint(0, 570))


def bg_skills(profile, rng):
    s = {"ar": None}
    if profile == "paramedic":
        s.update(adv_first_aid=cert_expiry(rng, False), paramedic=None)
        if rng.random() < 0.5:
            s["basic_first_aid"] = cert_expiry(rng, False)
    elif profile == "medical_other":
        s.update(adv_first_aid=cert_expiry(rng, False), basic_first_aid=cert_expiry(rng, False))
    elif profile == "security":
        s.update(security_license=cert_expiry(rng), security_guard=None)
        if rng.random() < 0.40: s["crowd_control"] = cert_expiry(rng)
        if rng.random() < 0.25: s["fire_safety"] = cert_expiry(rng)
        if rng.random() < 0.30: s["basic_first_aid"] = cert_expiry(rng)
    elif profile == "crowd":
        s["crowd_control"] = cert_expiry(rng)
        if rng.random() < 0.30: s["basic_first_aid"] = cert_expiry(rng)
        if rng.random() < 0.30: s["security_license"] = cert_expiry(rng)
    elif profile == "visitor":
        s["visitor_services"] = None
    elif profile == "facility":
        s["facility_tech"] = None
        if rng.random() < 0.90: s["fire_safety"] = cert_expiry(rng)
    en_p = 0.90 if profile == "visitor" else 0.60
    if rng.random() < en_p:
        s["en"] = None
    if rng.random() < (0.25 if profile == "visitor" else 0.08):
        s[rng.choice(["zh", "fr", "es", "ko"])] = None
    return s


def build_employees(rng):
    names, used = [], set()

    def new_name():
        while True:
            first = rng.choice(MALE + FEMALE)
            full = f"{first} {rng.choice(FAMILY)}"
            if first not in BANNED_FIRST and full not in used:
                used.add(full)
                return full

    bg = []
    for profile, n in PROFILES:
        for i in range(n):
            bg.append(dict(profile=profile, name=new_name(), skills=bg_skills(profile, rng),
                           planted=None, shift_type=None))
    rng.shuffle(bg)
    # shift type: alternate inside each profile (morning gets the floor)
    seen = {}
    for e in bg:
        c = seen.get(e["profile"], 0)
        e["shift_type"] = "morning" if c % 2 == 0 and c < (dict(PROFILES)[e["profile"]] // 2) * 2 else "evening"
        seen[e["profile"]] = c + 1
    # category: 23 volunteers from non-medical/non-security profiles, rest split loan/contractor
    pool = [i for i, e in enumerate(bg) if e["profile"] in ("visitor", "crowd", "ops", "facility")]
    vols = set(rng.sample(pool, 23))
    rest = [i for i in range(len(bg)) if i not in vols]
    rng.shuffle(rest)
    loans = set(rest[:26])
    for i, e in enumerate(bg):
        if i in vols:
            e["category"], e["source"] = "volunteer", rng.choice(VOLUNTEER_SRC)
        elif i in loans:
            e["category"], e["source"] = "loan", rng.choice(LOAN_SRC[e["profile"]])
        else:
            e["category"], e["source"] = "contractor", rng.choice(CONTRACTORS)
        e["dept"] = DEPT[e["profile"]]

    emps = list(bg)
    for key, p in PLANTED.items():
        emps.append(dict(profile="planted", name=p["name"], skills=dict(p["skills"]), planted=key,
                         shift_type="morning", category=p["category"], source=p["source"], dept=p["dept"]))
    rng.shuffle(emps)
    for i, e in enumerate(emps, 1):
        e["code"] = f"E{i:03d}"
    return emps


# --------------------------------------------------------------------------- #
# Roster
# --------------------------------------------------------------------------- #
def has_skill(emp, skill, d):
    if skill not in emp["skills"]:
        return False
    exp = emp["skills"][skill]
    return exp is None or exp >= d


def active_event_reqs(site, d, label):
    ws, we = shift_window(d, label)
    out = []
    for ev_id, s, skill, cnt in EVENT_REQS:
        if s != site:
            continue
        ev = EVENTS[ev_id]
        if ev[3] < we and ev[4] > ws:
            out.append((skill, cnt, ev_id))
    return out


def requirements(site, d, label, with_extra=True):
    reqs = [(sk, c) for sk, c in SITE[site][4]]
    reqs += [(sk, c) for sk, c, _ in active_event_reqs(site, d, label)]
    if with_extra:  # roster-building safety margin only; NOT a real site requirement
        reqs += EXTRA_REQS.get((site, d, label), [])
    merged = {}
    for sk, c in reqs:
        merged[sk] = max(merged.get(sk, 0), c)
    return [(sk, c) for sk, c in merged.items() if (site, d, label, sk) not in SKIP_REQS]


def build_roster(emps, rng):
    off = {}
    for e in emps:
        if e["planted"]:
            continue
        p = 0.30 if e["category"] == "volunteer" else (0.05 if e["profile"] == "paramedic" else 0.10)
        for di in range(3):
            if rng.random() < p:
                off.setdefault(e["code"], set()).add(di)
    off.setdefault(next(e["code"] for e in emps if e["planted"] == "khaled"), set()).add(1)

    fixed = {}  # (di, label) -> {site: [emp]}
    assign = {}  # (di,label,site) -> [emp]
    used = {}

    sites_order = sorted(SITES, key=lambda s: -sum(c for _, c in s[4]))
    for di, d in enumerate(DAYS):
        for label in ("morning", "evening"):
            key = (di, label)
            used[key] = set()
            for s in SITES:
                assign[(di, label, s[0])] = []
            # planted
            if label == "morning":
                for pk, p in PLANTED.items():
                    if di in p["roster"]:
                        emp = next(e for e in emps if e["planted"] == pk)
                        assign[(di, label, p["roster"][di])].append(emp)
                        used[key].add(emp["code"])
            pool = [e for e in emps if e["shift_type"] == label and not e["planted"]
                    and di not in off.get(e["code"], set())]
            # 1) skill requirements
            for s in sites_order:
                site = s[0]
                for skill, cnt in requirements(site, d, label):
                    have = sum(1 for e in assign[(di, label, site)] if has_skill(e, skill, d))
                    while have < cnt:
                        cands = [e for e in pool if e["code"] not in used[key] and has_skill(e, skill, d)]
                        if not cands:
                            raise Fail(f"no {skill} for {site} {d} {label}")
                        pick = rng.choice(cands)
                        assign[(di, label, site)].append(pick)
                        used[key].add(pick["code"])
                        have += 1
            # 2) head-count minimum
            for s in SITES:
                site = s[0]
                target = FORCE_SIZE.get((site, d, label), s[3])
                target = max(target, s[3])
                while len(assign[(di, label, site)]) < target:
                    cands = [e for e in pool if e["code"] not in used[key]]
                    if not cands:
                        raise Fail(f"headcount {site} {d} {label}")
                    # prefer generalists: keep rare skills free for gaps
                    cands.sort(key=lambda e: (sum(k in e["skills"] for k in
                                                  ("adv_first_aid", "paramedic")), rng.random()))
                    pick = cands[0]
                    assign[(di, label, site)].append(pick)
                    used[key].add(pick["code"])
            # 3) random surplus
            for e in pool:
                if e["code"] in used[key]:
                    continue
                if rng.random() < 0.45:
                    site = rng.choice([s[0] for s in SITES if (s[0], d, label) not in NO_EXTRAS
                                       and (s[0], d, label) not in FORCE_SIZE])
                    assign[(di, label, site)].append(e)
                    used[key].add(e["code"])
    return assign, off


def finalize(emps, assign, off, rng):
    """Hours, current site/zone, loan scope, absences."""
    by_emp = {e["code"]: [] for e in emps}
    for (di, label, site), lst in assign.items():
        for e in lst:
            by_emp[e["code"]].append((di, label, site))
    ot_pool = []
    for e in emps:
        rows = by_emp[e["code"]]
        k_in = len(rows)
        if e["planted"]:
            p = PLANTED[e["planted"]]
            k_out, ot = p["k_out"], p["ot"]
        elif e["category"] == "volunteer":
            k_out, ot = rng.randint(0, max(0, 3 - k_in)), 0
        else:
            k_out, ot = rng.randint(0, max(0, 5 - k_in)), 0
            if k_in + k_out == 5:
                ot_pool.append(e["code"])
        e["_k"] = (k_in, k_out, ot)
    for code in rng.sample(ot_pool, min(8, len(ot_pool))):
        e = next(x for x in emps if x["code"] == code)
        k_in, k_out, _ = e["_k"]
        e["_k"] = (k_in, k_out, rng.choice([4, 6, 8]))
    for e in emps:
        k_in, k_out, ot = e["_k"]
        e["hours"] = SHIFT_HOURS * (k_in + k_out) + ot
        first = sorted(by_emp[e["code"]])
        today = [r for r in first if r[0] == 0]
        if today:
            e["current_site"] = today[0][2]
            e["current_zone"] = SITE[today[0][2]][2]
        else:
            e["current_site"] = ""
            e["current_zone"] = rng.choice(ZONES)
        if e["category"] == "loan":
            if e["planted"]:
                sc = set(PLANTED[e["planted"]]["scope"])
            else:
                sc = {SITE[r[2]][2] for r in first}
                extra = rng.randint(0, 2)
                sc |= set(rng.sample(ZONES, extra))
                if len(sc) < 2:
                    sc |= set(rng.sample(ZONES, 2))
            e["scope"] = sorted(sc)
        else:
            e["scope"] = []
    return by_emp


def make_absences(emps, assign, off, by_emp, rng):
    rows = []
    # a few ordinary leave / sick records on days the person is already off
    cands = [(c, di) for c, ds in off.items() for di in ds]
    rng.shuffle(cands)
    for code, di in cands[:3]:
        d = DAYS[di]
        kind = rng.choice(["sick", "leave", "leave"])
        rows.append(dict(employee_code=code, kind=kind, starts_at=iso(at(d, 0)), ends_at=iso(at(d, 23, 59)),
                         reported_at=iso(at(DAYS[0], 8) - timedelta(days=rng.randint(1, 5)))))
    # optional sudden absence: someone assigned at S10 on the 13th evening does not show up
    victims = [e for e in assign[(2, "evening", "S10")]]
    victim = next(e for e in victims if not e["planted"])
    d = DAYS[2]
    rows.append(dict(employee_code=victim["code"], kind="no_show", starts_at=iso(at(d, 15)),
                     ends_at=iso(at(d, 23)), reported_at=iso(at(d, 14, 20))))
    return rows, victim


# --------------------------------------------------------------------------- #
# Reference evaluation (TEST ONLY — the backend owns the real engine)
# --------------------------------------------------------------------------- #
def travel(a, b):
    if a == b:
        return 5
    return 10 + 5 * (abs(ZONES.index(a) - ZONES.index(b)) - 1)


def evaluate(emps, assign, absences, site, di, label, req_skills):
    d = DAYS[di]
    ws, we = shift_window(d, label)
    zone = SITE[site][2]
    out = []
    emp_assign = {}
    for (i, l, s), lst in assign.items():
        for e in lst:
            emp_assign.setdefault(e["code"], []).append((i, l, s))
    abs_by = {}
    for a in absences:
        abs_by.setdefault(a["employee_code"], []).append(a)

    def headcount(i, l, s):
        return len(assign[(i, l, s)])

    for e in emps:
        mine = emp_assign.get(e["code"], [])
        this = [r for r in mine if r[0] == di and r[1] == label]
        res = dict(code=e["code"], name=e["name"], category=e["category"], eligible=True, exclusion="",
                   met=sum(1 for sk in req_skills if has_skill(e, sk, d)), total=len(req_skills),
                   travel=None, hours_after=None, surplus=None)
        # already at the target site
        if this and this[0][2] == site:
            res.update(eligible=False, exclusion="ALREADY_ASSIGNED"); out.append(res); continue
        # absence
        if any(datetime.fromisoformat(a["starts_at"]) < we and datetime.fromisoformat(a["ends_at"]) > ws
               for a in abs_by.get(e["code"], [])):
            res.update(eligible=False, exclusion="ABSENT"); out.append(res); continue
        # certificate (checked on the SHIFT date)
        expired = [sk for sk in req_skills if sk in CERT_CODES and sk in e["skills"] and not has_skill(e, sk, d)]
        if expired:
            res.update(eligible=False, exclusion="CERT_EXPIRED"); out.append(res); continue
        # secondment scope
        if e["category"] == "loan" and zone not in e["scope"]:
            res.update(eligible=False, exclusion="SCOPE_OUT"); out.append(res); continue
        # hours
        add = 0 if this else SHIFT_HOURS
        res["hours_after"] = e["hours"] + add
        if res["hours_after"] > CAT_CAP[e["category"]]:
            res.update(eligible=False, exclusion="HOURS_CAP"); out.append(res); continue
        # rest gap / second shift the same day
        bad = False
        for (i, l, s) in mine:
            if (i, l) == (di, label):
                continue
            ps, pe = shift_window(DAYS[i], l)
            gap_h = (ws - pe).total_seconds() / 3600 if pe <= ws else (ps - we).total_seconds() / 3600
            if gap_h < REST_MIN:
                bad = True
        if bad:
            res.update(eligible=False, exclusion="REST_GAP"); out.append(res); continue
        # moving someone away must not break the minimum / a requirement at their current site
        if this:
            cs = this[0][2]
            rest_staff = [x for x in assign[(di, label, cs)] if x["code"] != e["code"]]
            broke = len(rest_staff) < SITE[cs][3]
            for sk, cnt in requirements(cs, d, label, with_extra=False):
                if sum(1 for x in rest_staff if has_skill(x, sk, d)) < cnt:
                    broke = True
            if broke:
                res.update(eligible=False, exclusion="BREAKS_MINIMUM"); out.append(res); continue
            res["surplus"] = len(rest_staff) - SITE[cs][3]
        res["travel"] = travel(e["current_zone"], zone)
        out.append(res)
    el = [r for r in out if r["eligible"]]
    el.sort(key=lambda r: (-r["met"], r["travel"], -(CAT_CAP[r["category"]] - r["hours_after"]),
                           -(r["surplus"] if r["surplus"] is not None else -1)))
    for rank, r in enumerate(el, 1):
        r["rank"] = rank
        r["tag"] = "recommended" if rank == 1 else ""
        if r["hours_after"] >= CAT_CAP[r["category"]] - SOFT_MARGIN and rank > 1:
            r["tag"] = "backup"
    if len(el) > 1 and not el[1]["tag"]:
        el[1]["tag"] = "backup"
    return out


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate(emps, assign, absences, victim):
    errs = []
    code2e = {e["code"]: e for e in emps}
    abs_by = {}
    for a in absences:
        abs_by.setdefault(a["employee_code"], []).append(a)
    # coverage + skills
    for di, d in enumerate(DAYS):
        for label in SHIFTS:
            for s in SITES:
                site = s[0]
                staff = assign[(di, label, site)]
                if len(staff) < s[3]:
                    errs.append(f"below minimum {site} {d} {label}")
                if (site, d, label) not in FORCE_SIZE and (site, d, label) not in NO_EXTRAS:
                    pass
                for sk, cnt in requirements(site, d, label):
                    if sum(1 for e in staff if has_skill(e, sk, d)) < cnt:
                        errs.append(f"requirement {sk} {site} {d} {label}")
    # the planted event gap exists: nobody at S03 on event morning speaks Japanese
    s03 = assign[(1, "morning", "S03")]
    if any("ja" in e["skills"] for e in s03):
        errs.append("S03 incumbents speak Japanese (gap would not exist)")
    # one shift per day, one site per shift, rest gap, absences
    per_emp = {}
    for (di, label, site), lst in assign.items():
        for e in lst:
            per_emp.setdefault(e["code"], []).append((di, label, site))
    for code, rows in per_emp.items():
        seen = set()
        for di, label, site in rows:
            if (di, label) in seen:
                errs.append(f"double booked {code}")
            seen.add((di, label))
        days = [r[0] for r in rows]
        if len(days) != len(set(days)):
            errs.append(f"two shifts same day {code}")
        if len({r[1] for r in rows}) > 1:
            errs.append(f"mixed shift types {code}")
        for di, label, site in rows:
            ws, we = shift_window(DAYS[di], label)
            for a in abs_by.get(code, []):
                if datetime.fromisoformat(a["starts_at"]) < we and datetime.fromisoformat(a["ends_at"]) > ws:
                    if not (a["kind"] == "no_show"):
                        errs.append(f"assigned while absent {code}")
    # hours
    for e in emps:
        n = len(per_emp.get(e["code"], []))
        if e["hours"] < SHIFT_HOURS * n:
            errs.append(f"hours below scheduled {e['code']}")
        if e["hours"] > CAT_CAP[e["category"]]:
            errs.append(f"hours above cap {e['code']}")
    # no unintended extra Japanese speakers
    jp = [e["name"] for e in emps if "ja" in e["skills"]]
    if len(jp) != 4:
        errs.append(f"Japanese speakers != 4: {jp}")
    # counts
    cats = {}
    for e in emps:
        cats[e["category"]] = cats.get(e["category"], 0) + 1
    if len(emps) != 80 or cats != {"loan": 28, "contractor": 28, "volunteer": 24}:
        errs.append(f"unexpected composition {len(emps)} {cats}")
    # optional absence gap: S10 evening 13th ends below minimum once the absentee is removed
    present = [e for e in assign[(2, "evening", "S10")] if e["code"] != victim["code"]]
    if len(present) >= SITE["S10"][3]:
        errs.append("optional absence case does not create a gap")
    return errs


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def write_csv(name, header, rows):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    for attempt in range(1, 400):
        rng = random.Random(SEED + attempt - 1)
        try:
            emps = build_employees(rng)
            assign, off = build_roster(emps, rng)
        except Fail:
            continue
        by_emp = finalize(emps, assign, off, rng)
        absences, victim = make_absences(emps, assign, off, by_emp, rng)
        errs = validate(emps, assign, absences, victim)
        if errs:
            continue
        break
    else:
        raise SystemExit("could not build a valid dataset")
    print(f"built on attempt {attempt} (seed {SEED + attempt - 1})")

    # ---- reference tables
    write_csv("zone.csv", ["code", "name"], [[z, f"المنطقة {z}"] for z in ZONES])
    write_csv("zone_travel_time.csv", ["from_zone", "to_zone", "minutes"],
              [[a, b, travel(a, b)] for a in ZONES for b in ZONES])
    write_csv("skill.csv", ["code", "name_ar", "name_en", "kind"], SKILLS)

    # ---- people
    emps_sorted = sorted(emps, key=lambda e: e["code"])
    write_csv("employee.csv",
              ["employee_code", "name", "category", "source_entity", "department", "current_zone",
               "current_site_code", "hours_worked_this_week", "loan_scope_zones", "is_active"],
              [[e["code"], e["name"], e["category"], e["source"], e["dept"], e["current_zone"],
                e["current_site"], e["hours"], ";".join(e["scope"]), "true"] for e in emps_sorted])
    es_rows = []
    for e in emps_sorted:
        for sk, exp in sorted(e["skills"].items()):
            es_rows.append([e["code"], sk, exp.isoformat() if exp else ""])
    write_csv("employee_skill.csv", ["employee_code", "skill_code", "expires_on"], es_rows)

    # ---- sites
    write_csv("critical_site.csv", ["site_code", "name", "zone", "min_staff_required"],
              [[s[0], s[1], s[2], s[3]] for s in SITES])
    req_rows = [[s[0], sk, c, "", "أساسي"] for s in SITES for sk, c in s[4]]
    for ev_id, site, skill, cnt in EVENT_REQS:
        req_rows.append([site, skill, cnt, ev_id, f"مضاف بحدث: {EVENTS[ev_id][1]}"])
    write_csv("site_requirement.csv", ["site_code", "skill_code", "min_count", "event_id", "note"], req_rows)

    # ---- calendar / shifts / assignments / absences
    write_csv("calendar_event.csv", ["event_id", "kind", "title", "country", "starts_at", "ends_at", "source_ref"],
              [[i, v[0], v[1], v[2], iso(v[3]), iso(v[4]), v[5]] for i, v in EVENTS.items()])
    sh_rows = []
    for d in DAYS:
        for label in SHIFTS:
            ws, we = shift_window(d, label)
            sh_rows.append([d.isoformat(), label, iso(ws), iso(we)])
    write_csv("shift.csv", ["date", "label", "starts_at", "ends_at"], sh_rows)
    a_rows = []
    for (di, label, site), lst in sorted(assign.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        for e in sorted(lst, key=lambda x: x["code"]):
            a_rows.append([e["code"], site, DAYS[di].isoformat(), label, "confirmed"])
    write_csv("assignment.csv", ["employee_code", "site_code", "shift_date", "shift_label", "status"], a_rows)
    write_csv("absence.csv", ["employee_code", "kind", "starts_at", "ends_at", "reported_at"],
              [[a["employee_code"], a["kind"], a["starts_at"], a["ends_at"], a["reported_at"]] for a in absences])

    # ---- rules
    write_csv("category_rule.csv", ["category", "max_weekly_hours", "min_rest_hours", "enforce_zone_scope", "note"],
              [["loan", 48, REST_MIN, "true", "الإعارة محصورة في loan_scope_zones (افتراض)"],
               ["contractor", 48, REST_MIN, "false", "افتراض"],
               ["volunteer", 30, REST_MIN, "false", "افتراض"]])
    write_csv("rule_config.csv", ["key", "value", "description", "is_assumption"],
              [["soft_hours_margin", SOFT_MARGIN, "إذا بقي للموظف أقل من هذا الهامش (ساعات) قبل الحد يُوسم احتياطي", "true"]])

    # ---- reasons
    write_csv("reason_templates.csv", ["code", "kind", "template_ar", "template_en"], [
        ["REQ_MET", "positive", "يستوفي {met} من {total} متطلبات الموقع", "Meets {met} of {total} site requirements"],
        ["KEEPS_MIN_AT_SITE", "positive", "نقله لا يكسر الحد الأدنى في موقعه الحالي ({site})", "Moving them keeps {site} at its minimum"],
        ["NEAR", "positive", "الأقرب: على بعد {minutes} دقيقة من الموقع", "Only {minutes} min away"],
        ["HOURS_OK", "positive", "ساعاته بعد التكليف {hours_after} من {cap}", "{hours_after} of {cap} weekly hours after assignment"],
        ["NEAR_HOURS_CAP", "caution", "قريب من حد الساعات: {hours_after} من {cap}", "Close to the hours cap: {hours_after} of {cap}"],
        ["CERT_EXPIRED", "exclusion", "شهادة {skill} تنتهي في {date}، قبل موعد الوردية", "Certificate {skill} expires on {date}, before the shift"],
        ["HOURS_CAP", "exclusion", "سيتجاوز حد الساعات الأسبوعي ({hours_after} من {cap})", "Would exceed the weekly hours cap"],
        ["SCOPE_OUT", "exclusion", "نطاق إعارته لا يشمل المنطقة {zone}", "Secondment scope does not include zone {zone}"],
        ["ABSENT", "exclusion", "غائب أو في إجازة خلال الوردية", "Absent or on leave during the shift"],
        ["ALREADY_ASSIGNED", "exclusion", "مكلّف بالفعل في هذا الموقع", "Already assigned to this site"],
        ["REST_GAP", "exclusion", "فاصل الراحة أقل من {rest} ساعة", "Rest gap shorter than {rest} h"],
        ["BREAKS_MINIMUM", "exclusion", "نقله يكسر الحد الأدنى أو متطلبات موقعه الحالي ({site})", "Moving them would break the minimum at {site}"],
    ])

    # ---- gaps + expected results
    emp_by_key = {k: next(e for e in emps if e["planted"] == k) for k in PLANTED}
    G1_SKILLS = ["adv_first_aid", "paramedic", "ar", "ja"]
    res1 = evaluate(emps, assign, absences, "S03", 1, "morning", G1_SKILLS)
    res_by = {r["code"]: r for r in res1}
    exp = {"noura": (True, "", "recommended", 1), "khaled": (True, "", "backup", 2),
           "reem": (False, "CERT_EXPIRED", "", ""), "ahmed": (False, "SCOPE_OUT", "", "")}
    for k, (el, ex, tag, rank) in exp.items():
        r = res_by[emp_by_key[k]["code"]]
        assert r["eligible"] == el and r["exclusion"] == ex, (k, r)
        if el:
            assert r["rank"] == rank and r["tag"] == tag, (k, r)
    others_full = [r for r in res1 if r["met"] == 4 and r["eligible"]
                   and r["code"] not in {emp_by_key[k]["code"] for k in PLANTED}]
    assert not others_full

    G2_SITE_STAFF = assign[(2, "evening", "S10")]
    write_csv("gap_definitions.csv",
              ["gap_id", "site_code", "shift_date", "shift_label", "required_skills", "trigger", "trigger_ref",
               "detected_at", "deadline_at", "optional"],
              [["G1", "S03", EVENT_DAY.isoformat(), "morning", ";".join(G1_SKILLS), "calendar", "event_id=1 (Japan national day) + event_id=2",
                iso(at(DAYS[0], 16, 40)), iso(at(EVENT_DAY, 11)), "false"],
               ["G2", "S10", DAYS[2].isoformat(), "evening", "crowd_control", "absence",
                f"{victim['code']} no_show", iso(at(DAYS[2], 14, 20)), iso(at(DAYS[2], 15)), "true"]])

    dem = []
    for k in ("noura", "khaled", "reem", "ahmed"):
        e = emp_by_key[k]
        r = res_by[e["code"]]
        dem.append(["G1", e["code"], e["name"], e["category"], r["eligible"], r["exclusion"],
                    r.get("rank", ""), r.get("tag", ""), f"{r['met']}/{r['total']}", r["travel"] or "",
                    r["hours_after"] or ""])
    # next best background candidates (context for UI testing)
    others = sorted([r for r in res1 if r["eligible"] and r["code"] not in {emp_by_key[k]["code"] for k in PLANTED}],
                    key=lambda r: r["rank"])[:3]
    for r in others:
        e = code2 = next(x for x in emps if x["code"] == r["code"])
        dem.append(["G1", e["code"], e["name"], e["category"], r["eligible"], r["exclusion"], r["rank"],
                    r["tag"], f"{r['met']}/{r['total']}", r["travel"] or "", r["hours_after"] or ""])
    write_csv("demo_cases.csv",
              ["gap_id", "employee_code", "name", "category", "expected_eligible", "expected_exclusion_code",
               "expected_rank", "expected_tag", "requirements_met", "travel_minutes", "hours_after"], dem)

    # exclusion summary for G1 (helps the team see the variety)
    summary = {}
    for r in res1:
        summary[r["exclusion"] or "ELIGIBLE"] = summary.get(r["exclusion"] or "ELIGIBLE", 0) + 1
    print("G1 evaluation summary:", summary)
    return emps, assign, res1, emp_by_key, victim


if __name__ == "__main__":
    main()
