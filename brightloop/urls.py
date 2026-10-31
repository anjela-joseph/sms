from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from accounts import views as acc
from core import views as core
from core import finance_views as finance
from core import subscription_views
from core import catalog_views

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
    # app
    path("", core.home, name="home"),
    path("dashboard/", core.dashboard, name="dashboard"),
    path("billing/run/", core.run_billing_view, name="run_billing"),
    path("billing/runs/<int:pk>/", finance.billing_result, name="billing_result"),
    path("subscriptions/new/", core.new_subscription, name="new_subscription"),
    path("subscriptions/", subscription_views.subscription_list, name="subscription_list"),
    path("products/", catalog_views.product_list, name="product_list"),
    path("products/new/", catalog_views.product_form, name="product_create"),
    path("products/<int:pk>/edit/", catalog_views.product_form, name="product_edit"),
    path("plans/", catalog_views.plan_list, name="plan_list"),
    path("plans/new/", catalog_views.plan_form, name="plan_create"),
    path("plans/<int:pk>/edit/", catalog_views.plan_form, name="plan_edit"),
    path("subscriptions/<int:pk>/", subscription_views.subscription_detail, name="subscription_detail"),
    path("subscriptions/<int:pk>/action/", subscription_views.subscription_action, name="subscription_action"),
    path("invoices/<int:pk>/", core.invoice_detail, name="invoice_detail"),
    path("invoices/", finance.invoice_list, name="invoice_list"),
    path("payments/", finance.payment_list, name="payment_list"),
    path("discounts/", finance.discount_list, name="discount_list"),
    path("discounts/new/", finance.discount_edit, name="discount_create"),
    path("discounts/<int:pk>/edit/", finance.discount_edit, name="discount_edit"),
    path("portal/", core.portal, name="portal"),
    path("portal/subscriptions/<int:pk>/<str:action>/", core.portal_action, name="portal_action"),
]
