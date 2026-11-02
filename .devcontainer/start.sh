#!/usr/bin/env bash
# Runs every time the codespace starts: public site (8000), admin console (8001), worker + beat.
# setsid detaches the servers so they keep running after this start-up script exits.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p /tmp/bookbz
pkill -f "manage.py runserver" 2>/dev/null; pkill -f "celery -A config" 2>/dev/null
sleep 1
setsid nohup python manage.py runserver 0.0.0.0:8000 > /tmp/bookbz/web.log 2>&1 < /dev/null &
SITE_ROLE=admin setsid nohup python manage.py runserver 0.0.0.0:8001 > /tmp/bookbz/admin.log 2>&1 < /dev/null &
setsid nohup celery -A config worker -B -l info --concurrency 2 > /tmp/bookbz/celery.log 2>&1 < /dev/null &
for i in $(seq 1 20); do
  if curl -s -o /dev/null http://127.0.0.1:8000/healthz && curl -s -o /dev/null http://127.0.0.1:8001/healthz; then
    echo "BookBZ is running. Open the Ports tab: 8000 = customer site + /provider/, 8001 = admin console."
    exit 0
  fi
  sleep 1
done
echo "Still starting... if the links don't load, check: tail -20 /tmp/bookbz/web.log"
