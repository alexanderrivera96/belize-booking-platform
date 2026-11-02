"""WhatsApp / SMS delivery. "console" logs messages (development); "twilio" sends through Twilio.
WhatsApp is tried first when the customer chose it; failures fall back to SMS."""
import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings

log = logging.getLogger("notifications.send")


class TransientSendError(Exception):
    pass


@dataclass
class SendResult:
    channel: str
    message_id: str


def _twilio_send(to, body, whatsapp: bool) -> str:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"
    data = {
        "To": f"whatsapp:{to}" if whatsapp else to,
        "From": f"whatsapp:{settings.TWILIO_WHATSAPP_FROM}" if whatsapp else settings.TWILIO_SMS_FROM,
        "Body": body,
    }
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
    token = base64.b64encode(f"{settings.TWILIO_ACCOUNT_SID}:{settings.TWILIO_AUTH_TOKEN}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("sid", "")
    except urllib.error.HTTPError as exc:
        if exc.code >= 500 or exc.code == 429:
            raise TransientSendError(f"Twilio {exc.code}")
        raise ValueError(f"Twilio rejected the message ({exc.code})")
    except urllib.error.URLError as exc:
        raise TransientSendError(str(exc))


def send(to: str, body: str, preferred: str = "whatsapp") -> SendResult:
    if settings.MESSAGING_BACKEND != "twilio":
        log.info("[%s → %s] %s", preferred, to, body)
        return SendResult(preferred, "console")
    if preferred == "whatsapp" and settings.TWILIO_WHATSAPP_FROM:
        try:
            return SendResult("whatsapp", _twilio_send(to, body, whatsapp=True))
        except ValueError:
            log.info("WhatsApp failed for %s, falling back to SMS", to)
    return SendResult("sms", _twilio_send(to, body, whatsapp=False))
