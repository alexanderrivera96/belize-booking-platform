# Going live on Render

Render is the Stage 1 host from the architecture plan. `render.yaml` in this repo sets everything up in one go:

| Render service | What it is | Address (before your own domain) |
| --- | --- | --- |
| `bookbz-web` | Customer website, provider dashboard, API | `https://bookbz-web.onrender.com` (Render may add a suffix) |
| `bookbz-admin` | Admin console (MFA) | `https://bookbz-admin.onrender.com` |
| `bookbz-worker` | Messages, reminders, sweeps (one instance only) | — |
| `bookbz-db` | PostgreSQL 16 | internal |
| `bookbz-redis` | Key Value (Redis) for the job queue | internal |

All in the `virginia` (US East) region, the closest to Belize.

## 1. Create the services (about 15 minutes)

1. Sign up at render.com with your GitHub account and allow it to access `belize-booking-platform`.
2. **New → Blueprint**, pick the repo, branch `main`. Render reads `render.yaml`.
3. It asks for the values marked `sync: false`. Leave them all blank for now.
4. Click **Apply**. The first deploy takes 5–10 minutes. The web service runs the database migrations and loads the towns and categories automatically.
5. Open the `bookbz-web` address — you should see the home page. Sign-in will say "Sign-in codes aren't switched on yet" until step 3.

## 2. Create your admin account

1. In Render open **bookbz-admin → Shell** and run:
   ```bash
   python manage.py create_platform_admin +501XXXXXXX --name "Your Name" --superuser
   ```
   Choose a password of 15+ characters (use a password manager).
2. Open the `bookbz-admin` address, sign in with phone + password, leave the authenticator box empty, and scan the QR code with Google/Microsoft Authenticator. **Save the backup codes.**
3. When every admin has done this: **bookbz-admin → Environment →** set `ADMIN_ALLOW_SELF_ENROLL` to `0`.

## 3. Turn on sign-in codes and reminders (Twilio)

Customers and providers can't sign in until this is done.

1. Create a Twilio account and upgrade it (trial accounts only send to verified numbers).
2. **Verify → Services → Create**: name it "BookBZ". Turn on the **SMS** channel. Under **Fraud Guard** choose Standard, and under **Geo permissions** allow **Belize only**.
3. Messaging for reminders: Twilio supports Belize with an alphanumeric sender (no registration) or an international number. One-way only — every SMS already carries a "manage booking" link.
4. In Render go to **Environment Groups → bookbz-shared** and add:

   | Key | Value |
   | --- | --- |
   | `TWILIO_ACCOUNT_SID` | from the Twilio console |
   | `TWILIO_AUTH_TOKEN` | from the Twilio console |
   | `TWILIO_VERIFY_SERVICE_SID` | the Verify service's SID (starts with `VA`) |
   | `TWILIO_SMS_FROM` | `BookBZ` (alphanumeric sender) or your Twilio number |

   Then change `OTP_BACKEND` and `MESSAGING_BACKEND` to `twilio`. Render redeploys the three services.
5. Test: sign in with your own phone on the live site, make a booking, and check you receive the confirmation.

WhatsApp comes later: it needs a WhatsApp Business sender approved by Meta. Until `TWILIO_WHATSAPP_FROM` is set, the site only offers SMS.

## 4. Your own domain

1. Buy a domain (for example a `.bz` from belizenic.bz, or a `.com`).
2. **bookbz-web → Settings → Custom Domains**: add `yourdomain.com` and `www.yourdomain.com`. **bookbz-admin**: add `admin.yourdomain.com`. Render shows the DNS records to create at your domain registrar and issues HTTPS certificates automatically.
3. Environment values:
   - `bookbz-web`: `DJANGO_ALLOWED_HOSTS` = `yourdomain.com,www.yourdomain.com`, `PUBLIC_BASE_URL` = `https://yourdomain.com`
   - `bookbz-worker`: `PUBLIC_BASE_URL` = `https://yourdomain.com` (used in reminder links)
   - `bookbz-admin`: `DJANGO_ALLOWED_HOSTS` = `admin.yourdomain.com`

## 5. Add the first businesses

In the admin console: **+ Add service provider**. Fill in the business, the owner's mobile number and their services, and leave "List publicly" unticked. The owner signs in at `/provider/` with a code, checks their page, hours and prices, and you then list it (Businesses → select → "List publicly").

## Before inviting the public

- [ ] Sign-in codes and reminders tested on real Belize numbers (Digi and Smart)
- [ ] Admin MFA enrolled, `ADMIN_ALLOW_SELF_ENROLL=0`
- [ ] Privacy notice reviewed by a lawyer (Data Protection Act 2021) and the placeholder page replaced
- [ ] Render database backups checked (Render takes them on paid databases)
- [ ] Cloudflare in front of the domain, with Cloudflare Access on `admin.` (security plan, SEC3)
- [ ] Error alerts (e.g. Sentry) and uptime monitoring on `/healthz`

## Notes

- **Cost:** three small instances, a small Postgres database and free Key Value. Check Render's pricing page for current figures.
- **Updates:** every push to `main` redeploys automatically (CI runs the tests on GitHub first).
- **Worker:** the worker runs Celery beat inside it, so keep it at exactly **one** instance. Split beat into its own service before scaling up.
- **Payments** stay off (`PAYMENTS_ENABLED=0`) until the bank and DigiWallet integrations are built.
