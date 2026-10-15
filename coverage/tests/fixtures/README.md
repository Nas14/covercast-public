# Expected results, derived by hand BEFORE the Phase 5 engine was written

Committed on its own, before `eligibility.py` and `ranking.py` existed, so the
engine is tested against an answer it could not have produced itself.

## G1: S03, Monday 14 Oct 2030, morning. Slot needs {paramedic, adv_first_aid, ja} in one person.

Raw facts were read directly from the database (not through the engine).

| Who | Fact | First failing rule |
|---|---|---|
| E006, E018 | already assigned to S03 this shift | ALREADY_ASSIGNED |
| 74 others | do not speak Japanese (only E012, E053, E060, E061 do) | MISSING_SKILL |
| E060 Reem | holds adv_first_aid, expires 2030-10-13 < shift date 2030-10-14 | CERT_EXPIRED |
| E053 Ahmed | loan, scope {A, B}; S03 is zone C | SCOPE_OUT |
| E061 Noura | all valid; scope {C, D, E}; S12 has 5 (min 4), E068 keeps paramedic + adv there | eligible |
| E012 Khaled | all valid; contractor; no shift on the 14th; rest 16 h either side | eligible |

Totals: 2 eligible, 2 ALREADY_ASSIGNED, 74 MISSING_SKILL, 1 CERT_EXPIRED, 1 SCOPE_OUT = 80.

### Scores (policy.py weights 30/25/20/15/10; parts rounded to 0.01, score = sum of parts)

Noura (on shift at S12, zone D -> C = 10 min; hours 8 before window + 3 shifts x 8 = 32, moving adds nothing):
- proximity      30 x (25 - 10) / 20       = 22.50
- hours_headroom 25 x (48 - 32) / 48       =  8.33
- extra_skills   no nice-to-have defined    = 20.00
- donor_safety   leaves S12, importance 5: 15 x (5 - 5) / 4 = 0.00
- fairness       no replacements this week  = 10.00
- **score 60.83, rank 1, recommended**

Khaled (no shift that day -> call-in 60 min; hours 24 + 2 shifts x 8 + 8 = 48):
- proximity      60 > 25 -> clamped        =  0.00
- hours_headroom 25 x (48 - 48) / 48       =  0.00
- extra_skills                              = 20.00
- donor_safety   not assigned anywhere      = 15.00
- fairness                                  = 10.00
- **score 45.00, rank 2, backup**

### Baseline: "nearest qualified" (holds the skills and they are valid; ignores scope, hours, rest and the donor site)

Qualified: Noura 10 min, Ahmed 10 min (S05, zone B -> C), Khaled 60 min. Reem is not (expired).
Tie at 10 min, broken by employee code -> **E053 Ahmed**. He:
- violates SCOPE_OUT (zone C outside {A, B});
- leaves S05 (North Gate, min 3, has exactly 3) at 2: **short by 1**.
