from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from accounts import views as acc
from core import views as core
from core import finance_views as finance

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
    path("staff/new/", acc.add_staff, name="add_staff"),
    path("profile/", acc.profile, name="profile"),
    # app
    path("", core.home, name="home"),
    path("dashboard/", core.dashboard, name="dashboard"),
    path("billing/runs/<int:pk>/", finance.billing_result, name="billing_result"),
    path("billing/run/", core.run_billing_view, name="run_billing"),
    path("subscriptions/new/", core.new_subscription, name="new_subscription"),
    path("invoices/", finance.invoice_list, name="invoice_list"),
    path("invoices/<int:pk>/", core.invoice_detail, name="invoice_detail"),
    path("portal/", core.portal, name="portal"),
    path("portal/subscriptions/<int:pk>/<str:action>/", core.portal_action, name="portal_action"),
]
