from django import template

register = template.Library()

STATUS_BADGE = {
    "confirmed": "badge-ok", "completed": "badge", "pending_approval": "badge-warn", "awaiting_deposit": "badge-warn",
    "held": "badge", "no_show": "badge-danger", "declined": "badge-danger", "expired": "badge",
    "released": "badge", "cancelled_by_client": "badge-danger", "cancelled_by_provider": "badge-danger",
}


@register.filter
def bzd(cents):
    if cents is None or cents == "":
        return ""
    cents = int(cents)
    return f"BZ${cents // 100:,}" + (f".{cents % 100:02d}" if cents % 100 else "")


@register.filter
def duration(minutes):
    minutes = int(minutes or 0)
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h} h {m} min"
    return f"{h} h" if h else f"{m} min"


@register.filter
def status_badge(status):
    return STATUS_BADGE.get(status, "badge")


@register.filter
def initials(name):
    return "".join(w[0] for w in str(name).split()[:2]).upper()


@register.filter
def get(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None
