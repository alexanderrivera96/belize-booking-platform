from django import forms
from django.conf import settings

from .models import Channel


def channel_choices():
    """Hide WhatsApp until an approved WhatsApp sender is configured."""
    return Channel.choices if settings.WHATSAPP_ENABLED else [(Channel.SMS, "SMS")]


def default_channel():
    return Channel.WHATSAPP if settings.WHATSAPP_ENABLED else Channel.SMS


class ChannelChoiceMixin:
    channel_fields = ("channel", "preferred_channel")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for name in self.channel_fields:
            if name in self.fields:
                self.fields[name].choices = channel_choices()
                if not settings.WHATSAPP_ENABLED:
                    self.initial[name] = Channel.SMS


class PhoneForm(ChannelChoiceMixin, forms.Form):
    phone = forms.CharField(label="Mobile number", max_length=20,
                            widget=forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "tel",
                                                          "placeholder": "+501 600 1234"}))
    channel = forms.ChoiceField(label="Send my code by", choices=Channel.choices, initial=Channel.WHATSAPP,
                                widget=forms.RadioSelect)


class CodeForm(forms.Form):
    code = forms.CharField(label="6-digit code", max_length=8,
                           widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code",
                                                         "pattern": "[0-9]*", "class": "code-input"}))
    remember = forms.BooleanField(label="Keep me signed in on this device", required=False, initial=True)


class EmailForm(forms.Form):
    email = forms.EmailField(label="Email address")


class ProfileForm(ChannelChoiceMixin, forms.Form):
    full_name = forms.CharField(label="Your name", max_length=150)
    preferred_channel = forms.ChoiceField(label="Send reminders by", choices=Channel.choices,
                                          widget=forms.RadioSelect)
    email = forms.EmailField(label="Email (recommended)", required=False,
                             help_text="For receipts and to get back into your account if you change your number.")
