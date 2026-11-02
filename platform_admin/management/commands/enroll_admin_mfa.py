"""Enrol an authenticator for an admin from the server (when ADMIN_ALLOW_SELF_ENROLL is off).
Prints the otpauth:// URI once; add it to the admin's authenticator app in person."""
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import User


class Command(BaseCommand):
    help = "Create a confirmed TOTP device for an admin and print its setup URI."

    def add_arguments(self, p):
        p.add_argument("phone")

    def handle(self, phone, **kw):
        u = User.objects.filter(phone=phone, is_staff=True).first()
        if not u:
            raise CommandError("No admin with that phone.")
        TOTPDevice.objects.filter(user=u).delete()
        d = TOTPDevice.objects.create(user=u, name="Authenticator app", confirmed=True)
        self.stdout.write("Add this to the authenticator app (shown once):")
        self.stdout.write(d.config_url)
