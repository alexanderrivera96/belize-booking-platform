"""Tenant isolation, layers 1–3 (accounts design §2). Layer 4 is the cross-tenant test suite.

1. Resolve the caller's active membership from the business id in the URL, or 404.
2. Every queryset and write is scoped to that business.
3. Ids inside request bodies are accepted only if they belong to that business.
"""
from functools import wraps

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from rest_framework import serializers, viewsets
from rest_framework.exceptions import NotAuthenticated, NotFound
from rest_framework.permissions import BasePermission, IsAuthenticated

from businesses.models import BusinessMember

from .permissions import Cap, has_cap
from .sessions import DAY, strong_verification_within


def resolve_membership(user, business_id):
    if not user.is_authenticated:
        return None
    try:
        return (BusinessMember.objects.select_related("business", "business__booking_policy", "staff_profile")
                .filter(business_id=business_id, user=user, status=BusinessMember.Status.ACTIVE).first())
    except Exception:  # malformed UUID etc. — fail closed as "not found"
        return None


def attach(request, member):
    from zoneinfo import ZoneInfo

    from django.utils import timezone

    timezone.activate(ZoneInfo(member.business.timezone))   # forms and templates use the business's clock
    request.membership = member
    request.business = member.business
    request.staff = getattr(member, "staff_profile", None)


# ---------------------------------------------------------------- HTML views

def business_view(cap: Cap | None = None):
    """Decorator for provider-dashboard views taking `business_id`."""
    def deco(view):
        @wraps(view)
        def wrapped(request, business_id, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f"{reverse('accounts:login')}?next={request.get_full_path()}")
            member = resolve_membership(request.user, business_id)
            if member is None:
                raise Http404()
            if not strong_verification_within(request, settings.BUSINESS_REVERIFY_DAYS * DAY):
                return redirect(f"{reverse('accounts:reverify')}?next={request.get_full_path()}")
            attach(request, member)
            if cap is not None and not has_cap(member, cap):
                raise Http404()
            return view(request, business_id, *args, **kwargs)
        return wrapped
    return deco


# ---------------------------------------------------------------- API

class IsBusinessMember(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        member = resolve_membership(request.user, view.kwargs.get("business_id"))
        if member is None:
            raise NotFound()
        if not strong_verification_within(request, settings.BUSINESS_REVERIFY_DAYS * DAY):
            raise NotAuthenticated(code="reverify_required")
        attach(request, member)
        return True


class HasCapability(BasePermission):
    """Views declare capabilities = {"list": Cap.X, "create": Cap.Y, ...}. Missing action → denied."""
    def has_permission(self, request, view):
        caps = getattr(view, "capabilities", None)
        if caps is None:
            return False                       # fail closed: a scoped view must declare its rules
        action = getattr(view, "action", None) or request.method.lower()
        cap = caps.get(action, caps.get("*"))
        if cap is None:
            return False
        return has_cap(request.membership, cap)


class BusinessScopedViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsBusinessMember, HasCapability]
    capabilities: dict = {}

    def get_queryset(self):
        return self.queryset.filter(business=self.request.business)

    def perform_create(self, serializer):
        serializer.save(business=self.request.business)


class BusinessScopedPK(serializers.PrimaryKeyRelatedField):
    def get_queryset(self):
        return super().get_queryset().filter(business=self.context["request"].business)
