"""Seed the launch towns and categories (architecture plan: decisions table). Safe to re-run."""
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from businesses.models import Category, Town

TOWNS = [
    ("Corozal Town", "Corozal"), ("Orange Walk Town", "Orange Walk"), ("Belize City", "Belize"),
    ("Ladyville", "Belize"), ("Lord's Bank", "Belize"), ("Belmopan", "Cayo"), ("San Ignacio", "Cayo"),
    ("Santa Elena", "Cayo"), ("Benque Viejo", "Cayo"), ("Dangriga", "Stann Creek"), ("Punta Gorda", "Toledo"),
]
CATEGORIES = ["Barbers", "Hair, braids & locs", "Nail techs", "Eyelash techs", "Dental services", "Massage"]


class Command(BaseCommand):
    help = "Create launch towns and categories if missing."

    def handle(self, *args, **opts):
        for name, district in TOWNS:
            Town.objects.get_or_create(slug=slugify(name), defaults={"name": name, "district": district})
        for i, name in enumerate(CATEGORIES):
            Category.objects.get_or_create(slug=slugify(name), defaults={"name": name, "sort_order": i})
        self.stdout.write(self.style.SUCCESS(f"{Town.objects.count()} towns, {Category.objects.count()} categories"))
