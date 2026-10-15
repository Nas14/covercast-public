"""python manage.py session_report [--last-minutes 30]

After a usability session: how long each approval took, from the last time
the candidates screen was opened to the press of "approve", and how long the
employee took to answer. Run `load_demo --reset` before the next participant.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from coverage.services.sessions import approval_timings


def clock(dt):
    return timezone.localtime(dt).strftime("%H:%M:%S") if dt else "—"


def secs(n):
    return "—" if n is None else f"{n} ث"


class Command(BaseCommand):
    help = "Approval timings for usability sessions (screen opened -> approve -> employee answer)."

    def add_arguments(self, parser):
        parser.add_argument("--last-minutes", type=int, default=None,
                            help="Only approvals made in the last N minutes (default: all).")

    def handle(self, *args, last_minutes, **opts):
        since = timezone.now() - timedelta(minutes=last_minutes) if last_minutes else None
        rows = approval_timings(since)
        if not rows:
            self.stdout.write("لا توجد اعتمادات بعد" + (f" في آخر {last_minutes} دقيقة." if last_minutes else "."))
            return
        for r in rows:
            self.stdout.write(
                f"{r.site} | {r.shift} | {r.employee}\n"
                f"  فتح شاشة المرشحين {clock(r.viewed_at)}  ←  اعتمد {clock(r.approved_at)}"
                f"  =  زمن الاعتماد {secs(r.seconds_to_approve)}\n"
                f"  رد الموظف {clock(r.responded_at)}  =  بعد {secs(r.seconds_to_respond)}  (الحالة: {r.status})"
            )
        done = [r.seconds_to_approve for r in rows if r.seconds_to_approve is not None]
        if done:
            under = sum(1 for s in done if s < 60)
            self.stdout.write(f"\nاعتمادات أقل من 60 ثانية: {under} من {len(done)}")
