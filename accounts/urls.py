from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_phone, name="login"),
    path("login/code/", views.login_code, name="login_code"),
    path("login/email/", views.login_email, name="login_email"),
    path("login/email/code/", views.login_email_code, name="login_email_code"),
    path("reverify/", views.reverify, name="reverify"),
    path("logout/", views.logout_view, name="logout"),
    path("account/", views.account, name="account"),
    path("account/verify-email/", views.verify_email, name="verify_email"),
    path("account/sign-out-everywhere/", views.sign_out_everywhere, name="sign_out_everywhere"),
]
