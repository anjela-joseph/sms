from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import ProfileForm, SignupForm, StaffCreateForm
from .permissions import admin_required


def signup(request):
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Welcome to Brightloop!")
        return redirect("home")
    return render(request, "accounts/signup.html", {"form": form})


@admin_required
def add_staff(request):
    form = StaffCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"Staff member {user.email} added.")
        return redirect("add_staff")
    return render(request, "accounts/add_staff.html", {"form": form})



@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your details have been saved.")
        return redirect("profile")
    return render(request, "accounts/profile.html", {"form": form})