from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from .models import Plan, Product


class CatalogPagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.customer = User.objects.create_user("customer@example.test")
        cls.staff = User.objects.create_user("staff@example.test", role="staff")
        cls.admin = User.objects.create_user("admin@example.test", role="admin")
        cls.product = Product.objects.create(name="CRM", sales_price=100)
        cls.plan = Plan.objects.create(name="Monthly")

    def check_access(self, name, args=None, admin_only=False):
        url = reverse(name, args=args)
        for method in ("get", "post") if admin_only else ("get",):
            self.client.logout()
            self.assertRedirects(getattr(self.client, method)(url), reverse("login") + "?next=" + url,
                                 fetch_redirect_response=False)
            for user, expected in ((self.customer, 403), (self.staff, 403 if admin_only else 200), (self.admin, 200)):
                self.client.force_login(user)
                self.assertEqual(getattr(self.client, method)(url).status_code, expected)

    def test_product_list_access(self):
        self.check_access("product_list")

    def test_plan_list_access(self):
        self.check_access("plan_list")

    def test_product_create_access(self):
        self.check_access("product_create", admin_only=True)

    def test_product_edit_access(self):
        self.check_access("product_edit", [self.product.pk], admin_only=True)

    def test_plan_create_access(self):
        self.check_access("plan_create", admin_only=True)

    def test_plan_edit_access(self):
        self.check_access("plan_edit", [self.plan.pk], admin_only=True)

    def product_data(self):
        return {"name": "CRM Plus", "product_type": "service", "sales_price": "1000", "cost_price": "10",
                "is_active": "on", "variants-TOTAL_FORMS": "1", "variants-INITIAL_FORMS": "0",
                "variants-0-attribute": "Brand", "variants-0-value": "Odoo", "variants-0-extra_price": "560"}

    def test_product_create_with_variant_and_edit(self):
        self.client.force_login(self.admin)
        data = self.product_data()
        self.assertRedirects(self.client.post(reverse("product_create"), data), reverse("product_list"))
        product = Product.objects.get(name="CRM Plus")
        variant = product.variants.get()
        self.assertEqual(variant.price, 1560)
        self.assertContains(self.client.get(reverse("product_list")), "Brand: Odoo +560.00")
        data.update({"name": "Updated CRM", "variants-INITIAL_FORMS": "1", "variants-0-id": variant.pk,
                     "variants-0-product": product.pk})
        self.assertRedirects(self.client.post(reverse("product_edit", args=[product.pk]), data), reverse("product_list"))
        product.refresh_from_db()
        self.assertEqual(product.name, "Updated CRM")
        self.assertEqual(product.variants.count(), 1)

    def test_invalid_variant_does_not_save_product(self):
        self.client.force_login(self.admin)
        data = self.product_data()
        data["variants-0-extra_price"] = "invalid"
        self.assertEqual(self.client.post(reverse("product_create"), data).status_code, 200)
        self.assertFalse(Product.objects.filter(name="CRM Plus").exists())

    def test_plan_create_edit_and_date_validation(self):
        self.client.force_login(self.admin)
        data = {"name": "Annual", "price": "100", "billing_period": "yearly", "min_quantity": "1",
                "start_date": timezone.localdate().isoformat(), "renewable": "on", "pausable": "on"}
        self.assertRedirects(self.client.post(reverse("plan_create"), data), reverse("plan_list"))
        plan = Plan.objects.get(name="Annual")
        self.assertTrue(plan.pausable)
        data.update({"name": "Annual Plus", "end_date": "2000-01-01"})
        self.assertContains(self.client.post(reverse("plan_edit", args=[plan.pk]), data), "End date must")
        plan.refresh_from_db()
        self.assertEqual(plan.name, "Annual")
        data["end_date"] = data["start_date"]
        self.assertRedirects(self.client.post(reverse("plan_edit", args=[plan.pk]), data), reverse("plan_list"))
        plan.refresh_from_db()
        self.assertEqual(plan.name, "Annual Plus")

    def test_staff_cannot_post_valid_catalog_data_or_use_admin_to_edit(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(reverse("product_create"), self.product_data()).status_code, 403)
        self.assertEqual(Product.objects.count(), 1)
        for model, obj in (("product", self.product), ("plan", self.plan)):
            self.assertEqual(self.client.get(reverse(f"admin:core_{model}_add")).status_code, 403)
            self.assertEqual(self.client.post(reverse(f"admin:core_{model}_change", args=[obj.pk]), {}).status_code, 403)
            self.assertEqual(self.client.get(reverse(f"admin:core_{model}_change", args=[obj.pk])).status_code, 200)

    def test_staff_lists_hide_edit_links_and_missing_records_return_404(self):
        self.client.force_login(self.staff)
        self.assertNotContains(self.client.get(reverse("product_list")), reverse("product_create"))
        self.assertNotContains(self.client.get(reverse("plan_list")), reverse("plan_create"))
        self.client.force_login(self.admin)
        for name in ("product_edit", "plan_edit"):
            self.assertEqual(self.client.get(reverse(name, args=[99999])).status_code, 404)

    def test_empty_lists(self):
        self.product.delete()
        self.plan.delete()
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("product_list")), "No products yet")
        self.assertContains(self.client.get(reverse("plan_list")), "No plans yet")
