from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("role", User.Role.CUSTOMER)
        extra.setdefault("is_staff", extra["role"] in (User.Role.ADMIN, User.Role.STAFF))
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.update(role=User.Role.ADMIN, is_staff=True, is_superuser=True)
        return self._create_user(email, password, **extra)


class User(AbstractUser):
    """Login is by email. One email = one account (unique)."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        STAFF = "staff", "Internal staff"
        CUSTOMER = "customer", "Customer"

    username = None
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CUSTOMER, db_index=True)
    company = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    @property
    def is_admin(self):
        return self.is_superuser or self.role == self.Role.ADMIN

    @property
    def is_internal(self):
        return self.is_admin or self.role == self.Role.STAFF

    def __str__(self):
        name = self.get_full_name() or self.company
        return f"{name} <{self.email}>" if name else self.email
