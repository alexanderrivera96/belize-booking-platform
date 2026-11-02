#!/usr/bin/env bash
# Runs once when the codespace is created: install, migrate, load demo data.
set -euo pipefail
pip install --user -r requirements.txt
for i in $(seq 1 30); do python -c "import psycopg; psycopg.connect('postgresql://booking:booking@db:5432/booking').close()" 2>/dev/null && break; sleep 2; done
python manage.py migrate --noinput
python manage.py seed_demo
python manage.py setup_admin_groups
