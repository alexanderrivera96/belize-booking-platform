#!/usr/bin/env bash
# Runs every time the codespace starts: public site (8000), admin console (8001), worker + beat.
set -uo pipefail
mkdir -p /tmp/bookbz
pkill -f "manage.py runserver" 2>/dev/null; pkill -f "celery -A config" 2>/dev/null
nohup python manage.py runserver 0.0.0.0:8000 > /tmp/bookbz/web.log 2>&1 &
SITE_ROLE=admin nohup python manage.py runserver 0.0.0.0:8001 > /tmp/bookbz/admin.log 2>&1 &
nohup celery -A config worker -B -l info --concurrency 2 > /tmp/bookbz/celery.log 2>&1 &
echo "BookBZ is starting. Customer site: port 8000 · Admin console: port 8001 · Logs: /tmp/bookbz/"
