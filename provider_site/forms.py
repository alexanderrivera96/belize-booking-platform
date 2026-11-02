from decimal import ROUND_HALF_UP, Decimal

from django import forms

from businesses.models import Business, BookingPolicy, Service, Staff


class MoneyField(forms.DecimalField):
    """Shows BZ$ with cents, stores integer cents."""

    def __init__(self, **kw):
        kw.setdefault("min_value", 0)
        kw.setdefault("max_digits", 8)
        kw.setdefault("decimal_places", 2)
        super().__init__(**kw)

    def prepare_value(self, value):
        if isinstance(value, int):
            return f"{value / 100:.2f}"
        return value

    def clean(self, value):
        v = super().clean(value)
        return None if v is None else int((v * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class ServiceForm(forms.ModelForm):
    price_cents = MoneyField(label="Price (BZ$)")

    class Meta:
        model = Service
        fields = ["name", "description", "duration_min", "price_cents", "buffer_before_min", "buffer_after_min",
                  "active", "sort_order"]
        labels = {"duration_min": "Length (minutes)", "buffer_before_min": "Prep time before (minutes)",
                  "buffer_after_min": "Clean-up time after (minutes)", "active": "Bookable",
                  "sort_order": "Order on your page"}


class StaffForm(forms.ModelForm):
    services = forms.ModelMultipleChoiceField(queryset=Service.objects.none(), required=False,
                                              widget=forms.CheckboxSelectMultiple,
                                              label="Services this person performs")

    class Meta:
        model = Staff
        fields = ["display_name", "title", "active", "bookable_online", "sort_order"]
        labels = {"display_name": "Name clients see", "bookable_online": "Clients can book online",
                  "sort_order": "Order on your page"}

    def __init__(self, *a, business=None, **kw):
        super().__init__(*a, **kw)
        self.fields["services"].queryset = Service.objects.filter(business=business)
        if self.instance.pk:
            self.fields["services"].initial = list(self.instance.services.values_list("id", flat=True))


class InviteForm(forms.Form):
    phone = forms.CharField(label="Their mobile number", max_length=20,
                            widget=forms.TextInput(attrs={"inputmode": "tel", "placeholder": "+501 600 1234"}))
    staff = forms.ModelChoiceField(queryset=Staff.objects.none(), required=False,
                                   label="Link to a team member (optional)",
                                   help_text="Their logins then show this person's calendar.")

    def __init__(self, *a, business=None, **kw):
        super().__init__(*a, **kw)
        self.fields["staff"].queryset = Staff.objects.filter(business=business, member__isnull=True)


class TimeOffForm(forms.Form):
    staff = forms.ModelChoiceField(queryset=Staff.objects.none(), label="Who")
    starts = forms.DateTimeField(label="From", widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    ends = forms.DateTimeField(label="Until", widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    reason = forms.CharField(label="Note (only your team sees this)", required=False, max_length=120)

    def __init__(self, *a, staff_qs=None, **kw):
        super().__init__(*a, **kw)
        self.fields["staff"].queryset = staff_qs

    def clean(self):
        c = super().clean()
        if c.get("starts") and c.get("ends") and c["starts"] >= c["ends"]:
            raise forms.ValidationError("The end must be after the start.")
        return c


class WalkInForm(forms.Form):
    SOURCE = [("walk_in", "Walk-in"), ("phone", "Phone booking")]
    service = forms.ModelChoiceField(queryset=Service.objects.none())
    staff = forms.ModelChoiceField(queryset=Staff.objects.none(), label="With")
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time", "step": 300}))
    client_name = forms.CharField(label="Client name", max_length=150)
    client_phone = forms.CharField(label="Client mobile (optional)", max_length=20, required=False,
                                   help_text="Add it so they get a confirmation and reminders.")
    source = forms.ChoiceField(choices=SOURCE, widget=forms.RadioSelect, initial="walk_in")
    notes = forms.CharField(required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 2}),
                            label="Note (only your team sees this)")
    allow_overlap = forms.BooleanField(required=False, label="Allow this to overlap another booking")

    def __init__(self, *a, business=None, staff_qs=None, **kw):
        super().__init__(*a, **kw)
        self.fields["service"].queryset = Service.objects.filter(business=business, active=True)
        self.fields["staff"].queryset = staff_qs
        if not business.booking_policy.allow_provider_overlap:
            del self.fields["allow_overlap"]


class RescheduleForm(forms.Form):
    staff = forms.ModelChoiceField(queryset=Staff.objects.none(), label="With")
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time", "step": 300}))

    def __init__(self, *a, staff_qs=None, **kw):
        super().__init__(*a, **kw)
        self.fields["staff"].queryset = staff_qs


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Business
        fields = ["name", "tagline", "description", "address", "phone", "email"]
        labels = {"tagline": "Short tagline (shown in search)", "phone": "Business phone"}


class PolicyForm(forms.ModelForm):
    class Meta:
        model = BookingPolicy
        fields = ["requires_approval", "approval_timeout_min", "min_notice_min", "max_advance_days",
                  "slot_interval_min", "cancel_window_hours", "allow_provider_overlap", "staff_can_view_all"]
        labels = {
            "requires_approval": "I approve each booking before it's confirmed",
            "approval_timeout_min": "Requests expire after (minutes)",
            "min_notice_min": "Minimum notice for online bookings (minutes)",
            "max_advance_days": "How far ahead clients can book (days)",
            "slot_interval_min": "Start times every (minutes)",
            "cancel_window_hours": "Cancellation window (hours before)",
            "allow_provider_overlap": "Let my team double-book on purpose (e.g. colour processing time)",
            "staff_can_view_all": "Staff can see and manage everyone's calendar",
        }


class ClientNotesForm(forms.Form):
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}),
                            label="Private notes", help_text="Only your team sees these. Do not enter medical information.")
