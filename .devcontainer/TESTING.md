# Testing BookBZ in this codespace

The app starts automatically. Open the **Ports** tab (bottom panel):

| Port | What | Path |
| --- | --- | --- |
| 8000 | Customer site | `/` |
| 8000 | Provider dashboard | `/provider/` |
| 8001 | Admin console | `/` |

Test links are private to your GitHub account by default. To let someone else try it, right-click the port → **Port Visibility → Public** and send them the link. (This is a test environment with demo data — codes are shown on screen and no real WhatsApp/SMS is sent.)

## Demo logins

- **Customer:** any Belize mobile number (e.g. `610 1234`). The code appears in a yellow box on screen.
- **Business owners** (at `/provider/`): `600 1001` Fade Kings Barbershop · `600 2002` Crown & Coils (approval mode) · `600 3003` Island Nails & Lashes.
- **Admin console** (port 8001): phone `+5016009999`, password `admin-demo-password-change-me`. Leave the authenticator code empty the first time and scan the QR code with Google/Microsoft Authenticator.

## Useful commands (Terminal)

```bash
bash .devcontainer/start.sh            # restart all services
tail -f /tmp/bookbz/web.log            # see codes and messages that would be sent
python manage.py test tests            # run the test suite
```

Stop the codespace when you're done (github.com/codespaces → … → Stop) so it doesn't use your free hours.
