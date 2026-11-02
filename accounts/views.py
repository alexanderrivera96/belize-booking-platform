"""Phone-code login, email-code recovery login, business-area re-verification, account page."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from businesses.models import BusinessMember

from . import otp
from .forms import CodeForm, EmailForm, PhoneForm, ProfileForm
from .models import OtpRequest, User
from core.sessions import mark_verified, start_session


def _safe_next(request, default="/"):
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return nxt
    return default


def after_login_destination(user):
    members = BusinessMember.objects.filter(user=user, status__in=["active", "invited"])
    if members.exists():
        return "/provider/"
    return "/me/"


def login_phone(request):
    nxt = _safe_next(request, "")
    form = PhoneForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            phone = otp.start_phone_code(form.cleaned_data["phone"], channel=form.cleaned_data["channel"],
                                         ip=otp.client_ip(request))
        except otp.OtpError as e:
            form.add_error("phone", str(e))
        else:
            request.session["login_phone"] = phone
            request.session["login_channel"] = otp.effective_channel(form.cleaned_data["channel"])
            return redirect(f"/login/code/?next={nxt}" if nxt else "/login/code/")
    return render(request, "accounts/login.html", {"form": form, "next": nxt})


def login_code(request):
    phone = request.session.get("login_phone")
    if not phone:
        return redirect("accounts:login")
    nxt = _safe_next(request, "")
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if otp.check_phone_code(phone, form.cleaned_data["code"]):
            with transaction.atomic():
                user, created = User.objects.get_or_create(phone=phone)
                if user.phone_verified_at is None:
                    user.phone_verified_at = timezone.now()
                    user.save(update_fields=["phone_verified_at"])
            if not user.is_active or user.deleted_at:
                form.add_error("code", "This account is not active. Please contact support.")
            else:
                request.session.pop("login_phone", None)
                start_session(request, user, remember=form.cleaned_data["remember"], method="phone")
                if not user.full_name:
                    return redirect(f"/account/?welcome=1&next={nxt}")
                return redirect(nxt or after_login_destination(user))
        else:
            form.add_error("code", "That code didn't work. Check it and try again, or request a new one.")
    return render(request, "accounts/code.html", {
        "form": form, "phone": phone, "next": nxt, "dev_code": otp.dev_code_for(phone),
        "channel": request.session.get("login_channel", "whatsapp"),
    })


def login_email(request):
    """Recovery path for customers who can't receive codes on their phone."""
    form = EmailForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email, is_active=True).exists():
            try:
                otp.start_email_code(email, ip=otp.client_ip(request))
            except otp.OtpError as e:
                form.add_error("email", str(e))
                return render(request, "accounts/login_email.html", {"form": form})
        request.session["login_email"] = email
        messages.info(request, "If an account uses this email, we've sent it a code.")
        return redirect("accounts:login_email_code")
    return render(request, "accounts/login_email.html", {"form": form})


def login_email_code(request):
    email = request.session.get("login_email")
    if not email:
        return redirect("accounts:login_email")
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user and otp.check_email_code(email, form.cleaned_data["code"]):
            request.session.pop("login_email", None)
            start_session(request, user, remember=form.cleaned_data["remember"], method="email")
            messages.info(request, "Signed in by email. Update your mobile number on your account page if it changed.")
            return redirect("/me/")
        form.add_error("code", "That code didn't work.")
    return render(request, "accounts/code.html", {"form": form, "phone": email, "dev_code": otp.dev_code_for(email),
                                                  "channel": "email", "email_mode": True})


@login_required
def reverify(request):
    """Business area needs a phone verification within 30 days; step-up within 15 minutes."""
    nxt = _safe_next(request, "/provider/")
    phone = str(request.user.phone)
    form = CodeForm(request.POST or None)
    if request.method == "POST" and "send" in request.POST:
        try:
            otp.start_phone_code(phone, channel=request.user.preferred_channel, purpose=OtpRequest.Purpose.STEP_UP,
                                 ip=otp.client_ip(request), user=request.user)
            request.session["reverify_sent"] = True
            messages.info(request, "We've sent you a new code.")
        except otp.OtpError as e:
            messages.error(request, str(e))
        return redirect(f"/reverify/?next={nxt}")
    if request.method == "POST" and form.is_valid():
        if otp.check_phone_code(phone, form.cleaned_data["code"], purpose=OtpRequest.Purpose.STEP_UP):
            mark_verified(request, "phone")
            request.session.pop("reverify_sent", None)
            return redirect(nxt)
        form.add_error("code", "That code didn't work.")
    return render(request, "accounts/reverify.html", {
        "form": form, "next": nxt, "sent": request.session.get("reverify_sent"),
        "dev_code": otp.dev_code_for(phone),
    })


@require_POST
def logout_view(request):
    logout(request)
    return redirect("/")


@login_required
def account(request):
    user = request.user
    nxt = _safe_next(request, "")
    initial = {"full_name": user.full_name, "preferred_channel": user.preferred_channel,
               "email": user.email or user.pending_email or ""}
    form = ProfileForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        user.full_name = form.cleaned_data["full_name"].strip()
        user.preferred_channel = form.cleaned_data["preferred_channel"]
        email = (form.cleaned_data["email"] or "").lower().strip()
        fields = ["full_name", "preferred_channel"]
        if email and email != (user.email or "").lower():
            user.pending_email, user.pending_email_set_at = email, timezone.now()
            fields += ["pending_email", "pending_email_set_at"]
            try:
                otp.start_email_code(email, purpose=OtpRequest.Purpose.EMAIL_VERIFY, ip=otp.client_ip(request),
                                     user=user)
                messages.info(request, f"We sent a code to {email} — enter it below to verify.")
            except otp.OtpError as e:
                messages.error(request, str(e))
        user.save(update_fields=fields)
        messages.success(request, "Saved.")
        return redirect(nxt or "/account/")
    return render(request, "accounts/account.html", {
        "form": form, "welcome": request.GET.get("welcome"), "next": nxt, "code_form": CodeForm(),
        "dev_code": otp.dev_code_for(user.pending_email) if user.pending_email else None,
    })


@login_required
@require_POST
def verify_email(request):
    user = request.user
    code = request.POST.get("code", "")
    if not user.pending_email:
        return redirect("accounts:account")
    if otp.check_email_code(user.pending_email, code, purpose=OtpRequest.Purpose.EMAIL_VERIFY):
        try:
            with transaction.atomic():
                user.email, user.email_verified_at, user.pending_email = user.pending_email, timezone.now(), None
                user.save(update_fields=["email", "email_verified_at", "pending_email"])
                user.client_records.filter(email__isnull=True).update(email=user.email)
            messages.success(request, "Email verified.")
        except IntegrityError:
            messages.error(request, "This email can't be added — please contact support.")
    else:
        messages.error(request, "That code didn't work.")
    return redirect("accounts:account")


@login_required
@require_POST
def sign_out_everywhere(request):
    request.user.sign_out_everywhere()
    logout(request)
    messages.info(request, "You've been signed out on every device.")
    return redirect("/")
