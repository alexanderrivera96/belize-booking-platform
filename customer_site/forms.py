from django import forms
from django.conf import settings

from accounts.forms import ChannelChoiceMixin
from accounts.models import Channel


class CheckoutForm(ChannelChoiceMixin, forms.Form):
    full_name = forms.CharField(label="Your name", max_length=150)
    phone = forms.CharField(label="Mobile number", max_length=20,
                            widget=forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "tel",
                                                          "placeholder": "+501 600 1234"}))
    email = forms.EmailField(label="Email (recommended)", required=False,
                             help_text="For receipts and to get back into your account if you change your number.")
    preferred_channel = forms.ChoiceField(label="Send my code and reminders by", choices=Channel.choices,
                                          widget=forms.RadioSelect)
    notes = forms.CharField(label="Anything the business should know? (optional)", required=False, max_length=500,
                            widget=forms.Textarea(attrs={"rows": 2,
                                                         "placeholder": "Please don't include medical information."}))
    remember = forms.BooleanField(label="Keep me signed in on this device", required=False)

    def __init__(self, *args, signed_in=False, **kwargs):
        super().__init__(*args, **kwargs)
        if settings.CHECKOUT_EMAIL_REQUIRED:
            self.fields["email"].required = True
            self.fields["email"].label = "Email"
        if signed_in:
            for name in ("phone", "email", "preferred_channel", "remember"):
                del self.fields[name]
            self.fields["full_name"].required = False
            self.fields["full_name"].widget = forms.HiddenInput()
