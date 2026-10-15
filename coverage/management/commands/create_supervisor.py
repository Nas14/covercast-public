"""python manage.py create_supervisor <username> [--name "..."]

Creates (or updates) a supervisor: a normal user in the "supervisors" group.
No admin access. The password is never written in code or on the command
line: it is asked for twice, or read from COVERCAST_SUPERVISOR_PASSWORD
for scripted setups.
"""

import getpass
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from coverage import policy


class Command(BaseCommand):
    help = "Create or update a CoverCast supervisor (password is prompted, never passed as an argument)."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--name", default="", help="Display name, e.g. \"نوف\"")

    def handle(self, *args, username, name, **opts):
        password = os.environ.get("COVERCAST_SUPERVISOR_PASSWORD")
        if not password:
            password = getpass.getpass("كلمة المرور: ")
            if password != getpass.getpass("أعد كتابتها: "):
                raise CommandError("الكلمتان غير متطابقتين.")
        User = get_user_model()
        user = User.objects.filter(username=username).first() or User(username=username)
        try:
            validate_password(password, user)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
        user.first_name = name or user.first_name
        user.set_password(password)
        user.save()
        user.groups.add(Group.objects.get_or_create(name=policy.SUPERVISOR_GROUP)[0])
        self.stdout.write(self.style.SUCCESS(f"Supervisor ready: {username} (group '{policy.SUPERVISOR_GROUP}')"))
