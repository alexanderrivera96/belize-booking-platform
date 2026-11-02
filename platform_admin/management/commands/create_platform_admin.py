"""Create a platform admin (superuser) with a strong password. MFA is enrolled at first sign-in
(or with enroll_admin_mfa when self-enrolment is off)."""
import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from accounts.otp import OtpError, normalise_phone


class Command(BaseCommand):
    help = "Create a platform admin account."

    def add_arguments(self, p):
        p.add_argument("phone")
        p.add_argument("--name", default="")
        p.add_argument("--superuser", action="store_true", help="Full access (keep to at most 2 people)")
        p.add_argument("--group", choices=["Support", "Finance"])

    def handle(self, phone, name, superuser, group, **kw):
        try:
            phone = normalise_phone(phone)
        except OtpError as e:
            raise CommandError(str(e))
        pw = getpass.getpass("Password (15+ characters): ")
        validate_password(pw)
        u, _ = User.objects.get_or_create(phone=phone)
        u.full_name = name or u.full_name
        u.is_staff, u.is_superuser = True, superuser
        u.set_password(pw)
        u.save()
        if group:
            from django.contrib.auth.models import Group
            u.groups.add(Group.objects.get(name=group))
        self.stdout.write(self.style.SUCCESS(f"Admin {phone} ready. Sign in on the admin host to set up MFA."))
