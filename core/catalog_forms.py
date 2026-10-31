from django import forms
from django.forms import inlineformset_factory

from .models import Plan, Product, ProductVariant


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ("name", "product_type", "sales_price", "cost_price", "default_tax", "notes", "is_active")

    def clean_sales_price(self):
        price = self.cleaned_data["sales_price"]
        if price < 0:
            raise forms.ValidationError("Sales price cannot be negative.")
        return price

    def clean_cost_price(self):
        price = self.cleaned_data["cost_price"]
        if price < 0:
            raise forms.ValidationError("Cost price cannot be negative.")
        return price


VariantFormSet = inlineformset_factory(
    Product, ProductVariant, fields=("attribute", "value", "extra_price"), extra=1, can_delete=False)


class PlanForm(forms.ModelForm):
    class Meta:
        model = Plan
        fields = ("name", "price", "billing_period", "min_quantity", "start_date", "end_date",
                  "auto_close", "closable", "pausable", "renewable")
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}),
                   "end_date": forms.DateInput(attrs={"type": "date"})}

    def clean(self):
        data = super().clean()
        if data.get("price") is not None and data["price"] < 0:
            self.add_error("price", "Price cannot be negative.")
        if data.get("min_quantity") is not None and data["min_quantity"] < 1:
            self.add_error("min_quantity", "Minimum quantity must be at least 1.")
        if data.get("end_date") and data.get("start_date") and data["end_date"] < data["start_date"]:
            self.add_error("end_date", "End date must be on or after start date.")
        return data
