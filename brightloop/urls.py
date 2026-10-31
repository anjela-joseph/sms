from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from accounts import views as acc
from core import views as core
from core import finance_views as finance
from core import subscription_views

urlpatterns = [
    path("admin/", admin.site.urls),
    # auth
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("signup/", acc.signup, name="signup"),
    path("password-reset/", auth_views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/sent/", auth_views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("reset/done/", auth_views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("staff/", acc.staff_list, name="staff_list"),
    path("staff/new/", acc.add_staff, name="add_staff"),
    path("staff/<int:pk>/toggle/", acc.staff_toggle, name="staff_toggle"),
    path("profile/", acc.profile, name="profile"),
    # app
    path("", core.home, name="home"),
    path("dashboard/", core.dashboard, name="dashboard"),
    path("billing/run/", core.run_billing_view, name="run_billing"),
    path("subscriptions/new/", core.new_subscription, name="new_subscription"),
    path("subscriptions/", subscription_views.subscription_list, name="subscription_list"),
    path("subscriptions/<int:pk>/", subscription_views.subscription_detail, name="subscription_detail"),
    path("subscriptions/<int:pk>/action/", subscription_views.subscription_action, name="subscription_action"),
    path("invoices/<int:pk>/", core.invoice_detail, name="invoice_detail"),
    path("invoices/", finance.invoice_list, name="invoice_list"),
    path("portal/", core.portal, name="portal"),
    path("portal/subscriptions/<int:pk>/<str:action>/", core.portal_action, name="portal_action"),
]
