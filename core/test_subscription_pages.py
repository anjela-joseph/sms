from decimal import Decimal
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from .models import Plan, Product, ProductVariant, QuotationTemplate, Subscription, SubscriptionLine, Tax


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

    def test_create_page_access_rules(self):
        self.assert_internal_page(reverse("new_subscription"))

    def test_action_access_rules_and_post_only(self):
        url = reverse("subscription_action", args=[self.sub.pk])
        self.assertEqual(self.client.post(url, {"action": "quotation"}).status_code, 302)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.post(url, {"action": "quotation"}).status_code, 403)
        for user in (self.staff, self.admin):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 405)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "draft")
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        self.assertEqual(client.post(url, {"action": "quotation"}).status_code, 403)

    def test_lifecycle_errors_pause_resume_and_renew(self):
        self.client.force_login(self.staff)
        url = reverse("subscription_action", args=[self.sub.pk])
        response = self.client.post(url, {"action": "active"}, follow=True)
        self.assertContains(response, "Cannot move")
        for status in ("quotation", "confirmed", "active"):
            self.client.post(url, {"action": status})
            self.sub.refresh_from_db()
            self.assertEqual(self.sub.status, status)
        self.assertContains(self.client.post(url, {"action": "paused"}, follow=True), "does not allow pausing")
        self.plan.pausable = True
        self.plan.save()
        for status in ("paused", "active", "closed"):
            self.client.post(url, {"action": status})
            self.sub.refresh_from_db()
            self.assertEqual(self.sub.status, status)
        self.client.post(url, {"action": "renew"})
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "active")
        self.assertIsNotNone(self.sub.expiry_date)

    def test_plan_flags_hide_and_reject_actions(self):
        self.plan.renewable = False
        self.plan.closable = False
        self.plan.save()
        self.sub.status = "active"
        self.sub.save()
        self.client.force_login(self.staff)
        response = self.client.get(reverse("subscription_detail", args=[self.sub.pk]))
        for action in ("paused", "closed", "renew"):
            self.assertNotContains(response, f'value="{action}"')
            response = self.client.post(reverse("subscription_action", args=[self.sub.pk]), {"action": action}, follow=True)
            self.assertContains(response, 'class="msg error"')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "active")

    def test_create_rejects_unavailable_template_plan(self):
        template = QuotationTemplate.objects.create(name="Starter", plan=self.plan)
        self.client.force_login(self.staff)
        today = timezone.localdate()
        data = {"customer": self.customer.pk, "template": template.pk, "start_date": today.isoformat()}
        for start, end in ((today + timedelta(days=1), None), (today - timedelta(days=2), today - timedelta(days=1))):
            self.plan.start_date, self.plan.end_date = start, end
            self.plan.save()
            self.assertContains(self.client.post(reverse("new_subscription"), data), "not currently available")
            self.assertEqual(Subscription.objects.count(), 1)
        self.plan.start_date, self.plan.end_date = today, today
        self.plan.save()
        response = self.client.post(reverse("new_subscription"), data)
        created = Subscription.objects.latest("pk")
        self.assertRedirects(response, reverse("subscription_detail", args=[created.pk]))
