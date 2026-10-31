from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User


class SignupForm(UserCreationForm):
    """Public sign-up: always creates a customer."""

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name", "company")

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.CUSTOMER
        user.is_staff = False
        if commit:
            user.save()
        return user


class StaffCreateForm(UserCreationForm):
    """Admin-only: add an internal staff member."""

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.STAFF
        user.is_staff = True  # lets them into the back office with Staff-group permissions
        if commit:
            user.save()
            from django.contrib.auth.models import Group
            group, _ = Group.objects.get_or_create(name="Staff")
            user.groups.add(group)
        return user


class ProfileForm(forms.ModelForm):
    """Customer edits their own details (shown on invoices). Email stays fixed: it is the login."""

    class Meta:
        model = User
        fields = ("first_name", "last_name", "company", "phone", "address")
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}

    def clean_phone(self):
        phone = self.cleaned_data["phone"].strip()
        digits = phone.replace(" ", "").replace("-", "").lstrip("+")
        if phone and (not digits.isdigit() or not 7 <= len(digits) <= 15):
            raise forms.ValidationError("Enter a valid phone number (digits only, 7–15 long).")
        return phone