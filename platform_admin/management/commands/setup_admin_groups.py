"""Create the Support and Finance admin groups (accounts design §3). Superusers are is_superuser=True."""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

SUPPORT = {
    "view": ["business", "businessmember", "staff", "service", "bookingpolicy", "category", "town", "timeoff",
             "user", "customer", "appointment", "reminder", "messagelog", "otprequest", "messagingconsent",
             "adminactionlog", "logentry", "sitesettings"],
    "change": ["business", "user", "staff", "service", "bookingpolicy", "businessmember"],
    "add": [],
}
FINANCE = {"view": ["business", "appointment", "adminactionlog", "sitesettings"], "change": [], "add": []}


class Command(BaseCommand):
    help = "Create or refresh the Support and Finance admin groups."

    def handle(self, *a, **kw):
        for name, spec in [("Support", SUPPORT), ("Finance", FINANCE)]:
            g, _ = Group.objects.get_or_create(name=name)
            perms = []
            for verb, models in spec.items():
                for m in models:
                    perms += list(Permission.objects.filter(codename=f"{verb}_{m}"))
            g.permissions.set(perms)
            self.stdout.write(f"{name}: {len(perms)} permissions")
