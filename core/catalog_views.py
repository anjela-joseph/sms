"""Catalog maintenance for admins; internal staff have read-only lists."""
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from accounts.permissions import admin_required, internal_required
from .catalog_forms import PlanForm, ProductForm, VariantFormSet
from .models import Plan, Product


@internal_required
def product_list(request):
    products = Product.objects.select_related("default_tax").prefetch_related("variants").order_by("name", "pk")
    return render(request, "core/product_list.html", {
        "page_obj": Paginator(products, 50).get_page(request.GET.get("page")), "currency": settings.CURRENCY,
    })


@internal_required
def plan_list(request):
    return render(request, "core/plan_list.html", {
        "page_obj": Paginator(Plan.objects.order_by("name", "pk"), 50).get_page(request.GET.get("page")),
        "currency": settings.CURRENCY,
    })


@admin_required
def product_form(request, pk=None):
    product = get_object_or_404(Product, pk=pk) if pk is not None else Product()
    data = request.POST if request.method == "POST" else None
    form = ProductForm(data, instance=product)
    variants = VariantFormSet(data, instance=product, prefix="variants")
    if request.method == "POST":
        form_valid, variants_valid = form.is_valid(), variants.is_valid()
        if form_valid and variants_valid:
            with transaction.atomic():
                form.save()
                variants.save()
            messages.success(request, "Product saved.")
            return redirect("product_list")
    return render(request, "core/catalog_form.html", {
        "form": form, "variants": variants, "title": "Edit product" if pk else "New product",
        "cancel_url": "product_list",
    })


@admin_required
def plan_form(request, pk=None):
    plan = get_object_or_404(Plan, pk=pk) if pk is not None else Plan()
    form = PlanForm(request.POST if request.method == "POST" else None, instance=plan)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Plan saved.")
        return redirect("plan_list")
    return render(request, "core/catalog_form.html", {
        "form": form, "title": "Edit plan" if pk else "New plan", "cancel_url": "plan_list",
    })
