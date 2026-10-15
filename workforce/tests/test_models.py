"""Every database rule in workforce: one test that it accepts valid data,
one that it rejects the invalid case it exists for."""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from workforce.models import Absence, EmployeeSkill, Skill, ZoneDistance
from workforce.tests import factories as f


class ZoneDistanceTests(TestCase):
    def test_pair_is_unique(self):
        a, b = f.zone("A"), f.zone("B")
        ZoneDistance.objects.create(from_zone=a, to_zone=b, minutes=10)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ZoneDistance.objects.create(from_zone=a, to_zone=b, minutes=15)

    def test_reverse_pair_is_a_different_row(self):
        a, b = f.zone("A"), f.zone("B")
        ZoneDistance.objects.create(from_zone=a, to_zone=b, minutes=10)
        ZoneDistance.objects.create(from_zone=b, to_zone=a, minutes=10)
        self.assertEqual(ZoneDistance.objects.count(), 2)


class SkillTests(TestCase):
    def test_parent_cannot_be_self(self):
        s = f.skill("x")
        s.parent_id = s.pk
        with self.assertRaises(IntegrityError), transaction.atomic():
            s.save()

    def test_longer_cycle_rejected_by_clean(self):
        a = f.skill("a")
        b = f.skill("b", parent=a)
        a.parent = b
        with self.assertRaises(ValidationError):
            a.clean()

    def test_valid_chain_passes_clean(self):
        basic = f.skill("basic_first_aid", Skill.Kind.CERTIFICATION)
        adv = f.skill("adv_first_aid", Skill.Kind.CERTIFICATION, parent=basic)
        adv.clean()  # no exception
        self.assertEqual(list(basic.children.all()), [adv])


class CategoryRuleTests(TestCase):
    def test_zero_hours_cap_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            f.category("volunteer", max_weekly_hours=0)


class EmployeeTests(TestCase):
    def test_code_is_unique(self):
        f.employee("E001")
        with self.assertRaises(IntegrityError), transaction.atomic():
            f.employee("E001")

    def test_scope_zones_is_many_to_many(self):
        e = f.employee("E002", cat="loan")
        e.scope_zones.set([f.zone("C"), f.zone("D")])
        self.assertEqual(sorted(z.code for z in e.scope_zones.all()), ["C", "D"])

    def test_category_cannot_be_deleted_while_used(self):
        e = f.employee("E003")
        # PROTECT is enforced by Django before any SQL runs, so the error is
        # ProtectedError, not the database's IntegrityError.
        with self.assertRaises(ProtectedError):
            e.category.delete()


class EmployeeSkillTests(TestCase):
    def test_one_row_per_employee_and_skill(self):
        e, s = f.employee(), f.skill()
        f.give_skill(e, s)
        with self.assertRaises(IntegrityError), transaction.atomic():
            f.give_skill(e, s, date(2031, 1, 1))

    def test_validity_is_judged_on_the_given_day(self):
        es = EmployeeSkill(expires_on=date(2030, 10, 13))
        self.assertTrue(es.is_valid_on(date(2030, 10, 13)))   # last valid day
        self.assertFalse(es.is_valid_on(date(2030, 10, 14)))  # event day: expired

    def test_no_expiry_means_always_valid(self):
        self.assertTrue(EmployeeSkill(expires_on=None).is_valid_on(date(2099, 1, 1)))

    def test_skill_in_use_cannot_be_deleted(self):
        e, s = f.employee(), f.skill()
        f.give_skill(e, s)
        with self.assertRaises(ProtectedError):
            s.delete()


class AbsenceTests(TestCase):
    def test_end_must_be_after_start(self):
        e = f.employee()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Absence.objects.create(
                employee=e, kind="sick",
                starts_at=f.at(2030, 10, 15, 15), ends_at=f.at(2030, 10, 15, 15),
                reported_at=f.at(2030, 10, 15, 14),
            )

    def test_valid_absence(self):
        e = f.employee()
        Absence.objects.create(
            employee=e, kind="no_show",
            starts_at=f.at(2030, 10, 15, 15), ends_at=f.at(2030, 10, 15, 23),
            reported_at=f.at(2030, 10, 15, 14, 20),
        )
        self.assertEqual(e.absences.count(), 1)
