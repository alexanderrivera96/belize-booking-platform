# BookBZ — booking platform for Belize

A multi-business booking marketplace: customers find barbers, stylists, nail and lash techs, massage and dental services, book a time and get WhatsApp/SMS reminders; businesses run their day from a team calendar; the platform team onboards providers and runs the site.

Built to the project's architecture plan, accounts & permissions design, booking-engine design and security plan (Django 5.2 LTS, PostgreSQL, Celery + Redis).

## The three interfaces

| Interface | Where | Who | What it does |
| --- | --- | --- | --- |
| **Customer site** | public host `/` | Anyone | Search by service and town, business pages, live availability, book in one screen with a phone code (no password), My bookings, reschedule/cancel, manage-booking links from messages |
| **Provider dashboard** | public host `/provider/` | Business owners and staff | Today view, day/week calendar (auto-refresh), approve/decline requests, walk-ins and phone bookings, clients with private notes, services, team and invites, weekly hours, time off, booking rules, private calendar feed for phone calendars |
| **Admin console** | admin host (separate process, `SITE_ROLE=admin`) | Platform team | Add a service provider in one form, list/suspend/feature businesses, categories and towns, site settings (home headline, announcement banner, emergency booking pause), read-only bookings and message logs, audit log; password + authenticator (MFA) required |

**Payments are switched off** (`PAYMENTS_ENABLED=0`) until the bank and DigiWallet agreements are in place. Every deposit is BZ$0, so bookings confirm immediately (or go to the business for approval). The deposit model, deadline rules and state machine are already in the code, ready to switch on.

## Quick start (local)

Requirements: Python 3.13, PostgreSQL 16, Redis. Or use Docker (below).

```bash
pip install -r requirements.txt
cp .env.example .env                 # defaults work for local dev
export DJANGO_DEBUG=1
python manage.py migrate
python manage.py seed_demo           # 3 demo businesses + an admin (dev only)
python manage.py setup_admin_groups

python manage.py runserver 8000                         # customer site + provider dashboard
SITE_ROLE=admin python manage.py runserver 8001         # admin console
celery -A config worker -l info                         # messages, reminders
celery -A config beat -l info                           # every-minute sweeps (exactly one)
```

In development no messages are really sent: codes and WhatsApp/SMS texts are printed to the log, and the code also appears in a yellow banner on screen.

Demo logins:

- Customer: any Belize mobile number (e.g. `610 1234`) — enter the code shown on screen.
- Business owners: `600 1001` (Fade Kings Barbershop), `600 2002` (Crown & Coils, approval mode), `600 3003` (Island Nails & Lashes) at `/provider/`.
- Admin console (port 8001): phone `+5016009999`, password `admin-demo-password-change-me`; leave the authenticator code empty the first time and scan the QR code to enrol.

### Deploy for real customers

See **[DEPLOY.md](DEPLOY.md)** — one-click Render Blueprint (`render.yaml`), admin setup, Twilio, custom domain.

### Test online with GitHub Codespaces

On the repo page: **Code → Codespaces → Create codespace on main**. It installs everything, loads the demo data and starts the site (port 8000) and admin console (port 8001) on `https://<codespace>-<port>.app.github.dev` links. See `.devcontainer/TESTING.md`.

### Docker

```bash
cp .env.example .env
docker compose up --build    # web :8000, admin :8001, worker, beat, postgres, redis
```

## Project layout

```
config/          settings, urls_public.py (public host), urls_admin.py (admin host), celery.py
core/            tenancy layers, capability map, session policy, security headers, template filters
accounts/        User (phone identity), one-time codes (console / Twilio Verify), login + account pages
businesses/      Business, BusinessMember (logins), Staff (bookable people), Service, Schedule, TimeOff, BookingPolicy
clients/         each business's own client list + safe account linking
booking/         Appointment + exclusion constraint, availability engine, holds, lifecycle, sweeps, iCal feed
notifications/   reminders with quiet hours, WhatsApp→SMS sender, message log
customer_site/   customer interface
provider_site/   provider interface
platform_admin/  admin console: MFA login, onboarding, site settings, admin action log
api/             REST API v1 (for the future mobile app)
tests/           engine, race, reminders and cross-tenant tests
```

## How the important guarantees are enforced

- **No double-booking:** a PostgreSQL exclusion constraint on (staff, buffered time range) covers holds, pending requests and confirmed bookings in one table. Concurrent requests can't both win (tested with 20 threads).
- **Tenant isolation:** every dashboard page and API call resolves the caller's membership from the business id in the URL (non-members get 404), scopes every query to that business, and only accepts ids that belong to it. A test walks every business route as another business's owner and requires 404.
- **Staff vs owner:** staff see their own calendar and clients; services, team, hours and settings are owner-only. Policy and team changes need a fresh phone code (15 minutes); the dashboard needs one every 30 days; an email-code sign-in never opens the business area.
- **Reminders:** stored as rows, sent by a skip-locked loop (no double sends), re-checked at send time, moved out of 9 p.m.–8 a.m., cancelled and re-created on reschedule.
- **Admin:** separate host and cookie, password + TOTP (backup codes), 30-minute idle timeout, no delete permissions, masked phone numbers, audit log on core models.

## API

`/api/v1/` — public: `public/categories`, `public/search`, `public/businesses/{slug}`, `…/availability?service=&staff=&date=`, `public/holds`, `public/holds/{id}/confirm` (Idempotency-Key); auth: `auth/otp/start`, `auth/otp/verify`, `auth/logout`; customer: `me/appointments/…`, `me/memberships`; business: `businesses/{id}/services|staff|schedules|appointments/…` (approve, decline, cancel, no-show, complete).

## Tests

```bash
DJANGO_DEBUG=1 python manage.py test tests
```

CI (GitHub Actions) runs the tests against PostgreSQL, checks migrations, and runs `check --deploy`.

## Going live — still to do

- Twilio account: Verify service (WhatsApp + SMS, Fraud Guard on, Belize-only geo permissions), WhatsApp sender and approved templates; set `OTP_BACKEND=twilio`, `MESSAGING_BACKEND=twilio`.
- Payments (after the bank meetings): DigiWallet and card-gateway `PaymentProvider`s, webhooks, then `PAYMENTS_ENABLED=1`.
- Two-way Google/Outlook sync (the read-only iCal feed is in place).
- Production secrets, `ADMIN_ALLOW_SELF_ENROLL=0` once admins are enrolled, Cloudflare in front of both hosts (Access on the admin host), Sentry.
- Legal review of the privacy notice under Belize's Data Protection Act 2021.
