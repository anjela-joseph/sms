from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from .models import Plan, Product, ProductVariant, Subscription, SubscriptionLine, Tax


class SubscriptionPagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.customer = User.objects.create_user("customer@example.test", first_name="Ada", company="Loop Labs")
        cls.staff = User.objects.create_user("staff@example.test", role="staff")
        cls.admin = User.objects.create_user("admin@example.test", role="admin")
        cls.plan = Plan.objects.create(name="Monthly", price="100.00")
        cls.sub = Subscription.objects.create(customer=cls.customer, plan=cls.plan)
        tax = Tax.objects.create(name="GST", rate="18.00")
        product = Product.objects.create(name="CRM", sales_price=Decimal("1000.00"), default_tax=tax)
        variant = ProductVariant.objects.create(product=product, attribute="Brand", value="Odoo", extra_price=Decimal("560.00"))
        SubscriptionLine.objects.create(subscription=cls.sub, product=product, variant=variant, quantity=2)

    def assert_internal_page(self, url):
        self.client.logout()
        self.assertRedirects(self.client.get(url), reverse("login") + "?next=" + url, fetch_redirect_response=False)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(url).status_code, 403)
        for user in (self.staff, self.admin):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_list_access_rules(self):
        self.assert_internal_page(reverse("subscription_list"))

    def test_detail_access_rules(self):
        self.assert_internal_page(reverse("subscription_detail", args=[self.sub.pk]))

    def test_search_and_combined_filters(self):
        self.client.force_login(self.staff)
        url = reverse("subscription_list")
        for query in (self.sub.number, "Ada", "Loop Labs", "customer@example.test"):
            response = self.client.get(url, {"q": query, "status": "draft", "plan": self.plan.pk})
            self.assertEqual(list(response.context["page_obj"]), [self.sub])
        for params in ({"status": "active"}, {"plan": "bad"}, {"plan": "9" * 30}, {"q": "missing"}):
            self.assertContains(self.client.get(url, params), "No subscriptions found")

    def test_pagination_preserves_filters_and_fetches_related_objects(self):
        for _ in range(50):
            Subscription.objects.create(customer=self.customer, plan=self.plan)
        self.client.force_login(self.staff)
        # Session + user + count + subscriptions (joined customer/plan) + plan choices.
        with self.assertNumQueries(5):
            response = self.client.get(reverse("subscription_list"), {"q": "Loop Labs", "status": "draft", "plan": self.plan.pk})
        self.assertEqual(len(response.context["page_obj"]), 50)
        self.assertContains(response, "q=Loop+Labs&amp;status=draft&amp;plan=")
        response = self.client.get(reverse("subscription_list"), {"page": 2})
        self.assertEqual(len(response.context["page_obj"]), 1)
        self.assertEqual(self.client.get(reverse("subscription_list"), {"page": "bad"}).status_code, 200)

    def test_detail_lines_totals_and_missing_record(self):
        self.client.force_login(self.staff)
        with self.assertNumQueries(4):
            response = self.client.get(reverse("subscription_detail", args=[self.sub.pk]))
        self.assertContains(response, "Brand: Odoo")
        self.assertContains(response, "30 days")
        self.assertEqual(response.context["subtotal"], Decimal("3220.00"))
        self.assertEqual(response.context["tax_total"], Decimal("561.60"))
        self.assertEqual(response.context["total"], Decimal("3781.60"))
        self.assertEqual(self.client.get(reverse("subscription_detail", args=[99999])).status_code, 404)

    def test_detail_empty_lines(self):
        self.sub.lines.all().delete()
        self.client.force_login(self.staff)
        response = self.client.get(reverse("subscription_detail", args=[self.sub.pk]))
        self.assertContains(response, "No product lines")
        self.assertEqual(response.context["total"], Decimal("100.00"))
