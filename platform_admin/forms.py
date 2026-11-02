from datetime import time

from django import forms
from django.contrib.auth import authenticate
from django_otp import match_token, user_has_device

from accounts import otp as codes
from businesses.models import Category, Town


class AdminLoginForm(forms.Form):
    """Phone + strong password, then (if enrolled) a 6-digit authenticator code. SMS codes never work here."""
    phone = forms.CharField(label="Phone", max_length=20)
    password = forms.CharField(label="Password", widget=forms.PasswordInput)
    otp_token = forms.CharField(label="Authenticator code", required=False, max_length=12,
                                widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric"}),
                                help_text="From your authenticator app, or a backup code.")

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user = None
        self.device = None
        super().__init__(*args, **kwargs)

    def clean(self):
        c = super().clean()
        try:
            phone = codes.normalise_phone(c.get("phone", "")) if c.get("phone") else ""
        except codes.OtpError:
            phone = c.get("phone", "")
        user = authenticate(self.request, phone=phone, password=c.get("password"))
        if user is None or not user.is_active or not user.is_staff:
            raise forms.ValidationError("Those details don't match an admin account.")
        if user_has_device(user, confirmed=True):
            device = match_token(user, (c.get("otp_token") or "").replace(" ", ""))
            if device is None:
                raise forms.ValidationError("Enter a valid authenticator or backup code.")
            self.device = device
        self.user = user
        return c


class MfaSetupForm(forms.Form):
    token = forms.CharField(label="6-digit code from your app", max_length=6,
                            widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}))


class OnboardProviderForm(forms.Form):
    business_name = forms.CharField(max_length=150)
    category = forms.ModelChoiceField(queryset=Category.objects.filter(active=True))
    town = forms.ModelChoiceField(queryset=Town.objects.filter(active=True))
    address = forms.CharField(max_length=255, required=False)
    tagline = forms.CharField(max_length=160, required=False)
    business_phone = forms.CharField(max_length=20, required=False)
    owner_name = forms.CharField(max_length=150)
    owner_phone = forms.CharField(max_length=20, help_text="Belize mobile. They sign in with a code sent here.")
    owner_email = forms.EmailField(required=False)
    owner_is_bookable = forms.BooleanField(required=False, initial=True,
                                           label="Owner also takes appointments (solo provider)")
    requires_approval = forms.BooleanField(required=False, label="Owner approves each booking")
    default_hours = forms.BooleanField(required=False, initial=True, label="Start with Mon–Sat 9:00–17:00 hours")
    services = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}),
                               help_text="Optional. One per line: name, minutes, price — e.g. “Skin fade, 45, 35”.")
    list_now = forms.BooleanField(required=False, label="List publicly right away")
    send_welcome = forms.BooleanField(required=False, initial=True, label="Send the owner a welcome message")

    def clean_owner_phone(self):
        try:
            return codes.normalise_phone(self.cleaned_data["owner_phone"])
        except codes.OtpError as e:
            raise forms.ValidationError(str(e))

    def clean_services(self):
        rows = []
        for i, line in enumerate((self.cleaned_data.get("services") or "").splitlines(), 1):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            try:
                name, minutes, price = parts[0], int(parts[1]), round(float(parts[2].replace("$", "")) * 100)
                if not name or minutes < 5 or price < 0:
                    raise ValueError
            except (ValueError, IndexError):
                raise forms.ValidationError(f"Line {i}: use “name, minutes, price”.")
            rows.append((name, minutes, price))
        return rows


DEFAULT_HOURS = [(wd, time(9), time(17)) for wd in range(6)]
