from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import ProfileForm, SignupForm, StaffCreateForm
from .models import User
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
        return redirect("staff_list")
    return render(request, "accounts/add_staff.html", {"form": form})



@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your details have been saved.")
        return redirect("profile")
    return render(request, "accounts/profile.html", {"form": form})




@admin_required
def staff_list(request):
    staff = User.objects.filter(role__in=[User.Role.ADMIN, User.Role.STAFF]).order_by("role", "email")
    return render(request, "accounts/staff_list.html", {"staff": staff})


@admin_required
@require_POST
def staff_toggle(request, pk):
    member = get_object_or_404(User, pk=pk, role__in=[User.Role.ADMIN, User.Role.STAFF])
    if member == request.user:
        messages.error(request, "You can't switch off your own access.")
    else:
        member.is_active = not member.is_active
        member.save(update_fields=["is_active"])
        state = "restored" if member.is_active else "switched off"
        messages.success(request, f"Access for {member.email} {state}.")
    return redirect("staff_list")