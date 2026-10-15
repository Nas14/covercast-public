"""The concurrency gate: two approvals at the same moment, two real threads,
two database connections, one SQLite file (settings DATABASES TEST NAME).

To make the race certain rather than lucky, both threads are held at a
barrier placed AFTER they would have read the gap and BEFORE they write.
- With BEGIN IMMEDIATE (our setting) the second thread never reaches the
  barrier while the first is inside: it waits at the door. The barrier times
  out, the first commits, and the second then reads "already filled".
- Without it (sabotage), both pass the barrier having read "gap = 1".
"""

import threading
from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase

from coverage import importer
from coverage.models import Assignment, Shift, Site
from coverage.services import approval
from coverage.services.errors import CoverCastError
from coverage.tests.test_approval import BEFORE_G1


class TwoSupervisorsAtOnce(TransactionTestCase):
    def setUp(self):
        importer.load_folder(settings.COVERCAST_DEMO_DATA_DIR)
        self.sup = get_user_model().objects.create_user("supervisor")
        self.site = Site.objects.get(code="S03")
        self.shift = Shift.objects.get(date=date(2030, 10, 14), slot="morning")

    def race(self, *requests):
        barrier = threading.Barrier(len(requests), timeout=1.5)
        real_gap_for = approval.gap_for

        def gap_then_wait(*a, **kw):
            gap = real_gap_for(*a, **kw)
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return gap

        outcomes = [None] * len(requests)

        def run(i, code, key):
            try:
                outcomes[i] = approval.approve(self.site, self.shift, code, idempotency_key=key,
                                               supervisor=self.sup, now=BEFORE_G1)
            except Exception as exc:  # noqa: BLE001 - every outcome is inspected below
                outcomes[i] = exc
            finally:
                connection.close()

        approval.gap_for = gap_then_wait
        try:
            threads = [threading.Thread(target=run, args=(i, *r)) for i, r in enumerate(requests)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        finally:
            approval.gap_for = real_gap_for
        return outcomes

    def replacements(self):
        return Assignment.objects.filter(site=self.site, shift=self.shift, origin="replacement",
                                         status__in=["planned", "confirmed"])

    def test_two_candidates_one_slot_gives_one_success_and_one_409(self):
        out = self.race(("E061", "A"), ("E012", "B"))
        wins = [o for o in out if isinstance(o, approval.ApprovalResult)]
        refusals = [o for o in out if isinstance(o, CoverCastError)]
        self.assertEqual(len(wins), 1, out)
        self.assertEqual([r.code for r in refusals], ["GAP_ALREADY_FILLED"], out)
        self.assertEqual(self.replacements().count(), 1)

    def test_double_click_in_parallel_gives_one_assignment(self):
        out = self.race(("E061", "same"), ("E061", "same"))
        self.assertTrue(all(isinstance(o, approval.ApprovalResult) for o in out), out)
        self.assertEqual(sorted(o.replayed for o in out), [False, True])
        self.assertEqual(out[0].assignment.pk, out[1].assignment.pk)
        self.assertEqual(self.replacements().count(), 1)
