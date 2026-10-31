# Brightloop: Subscription Management System

Plans, subscriptions, recurring billing, discounts, payments and revenue reporting in one Django + PostgreSQL app.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
createdb brightloop                                     # or create it in pgAdmin
export POSTGRES_DB=brightloop POSTGRES_USER=postgres POSTGRES_PASSWORD=postgres   # Windows: set ...
python manage.py migrate
python manage.py seed_demo            # 10,000 subscriptions + invoice history, ~10 s
python manage.py runserver
```

Without `POSTGRES_DB`, the app falls back to SQLite, which is useful only if Postgres won't start.

| Role | Login | Password |
|---|---|---|
| Admin | admin@brightloop.test | Admin@12345 |
| Staff | staff@brightloop.test | Staff@12345 |
| Customer | demo@customer.test | Demo@12345 |

Tests: `python manage.py test` (password rule, variant pricing + tax, no double billing, catch-up billing, discounts and usage limit, payments → Paid, invalid status moves, role access, signup rules).

## How the brief maps to the code

| Scene | Where |
|---|---|
| Sign-up, unique email, password rule, reset link | `accounts/` (`validators.py`, `forms.py`); reset uses Django's signed, expiring tokens. Emails print to the console. |
| Roles: admin / staff / customer; only admin adds staff | `accounts/models.py` (`role`), `accounts/permissions.py`, admin `has_*_permission` |
| Products, variants (+560 for Odoo), plans with close/pause/renew flags | `core/models.py`: `Product`, `ProductVariant`, `Plan` |
| Quotation templates, subscription numbers, Draft→Quotation→Confirmed→Active→Closed | `QuotationTemplate`, `Subscription.TRANSITIONS`, `billing.change_status` |
| Billing day: invoices generated automatically, never twice, never skipped | `billing.run_billing` + DB `UniqueConstraint(subscription, period_start)`; `next_billing_date` catches up missed periods |
| Taxes from admin rules | `Tax` on each line, computed in `billing.build_invoice` |
| Discounts: fixed / %, min purchase / qty, dates, usage limit, product or whole subscription | `Discount` + `billing.pick_discount` (auto-applied, usage counted in the same transaction) |
| Invoice confirm / cancel / send / print | `/invoices/<id>/` and admin actions |
| Payments: method, amount, date, note; paid vs outstanding vs overdue | `billing.record_payment`; overdue = confirmed and past due date (computed, never stored) |
| Finance dashboard with filters, under 2 s on thousands of rows | `/dashboard/`; indexed columns, aggregate queries, paginated lists. Measured under 200 ms on 10k subscriptions / 23k invoices |
| Customer portal | `/portal/` (own subscriptions and invoices only; pause / resume / renew / cancel when the plan allows) |

Scheduled billing: `python manage.py run_billing` (cron: `0 6 * * * python manage.py run_billing`). Safe to run any number of times.

## Demo script (5 minutes)

1. **Sign up** at `/signup/` with `weakpass`: rejected. Then use a strong password. Try the same email again: rejected.
2. **Admin** → Back office → Products: show *Brightloop CRM* with the **Odoo +560** variant, and Plans with the pausable/closable/renewable flags.
3. **Staff** → *New subscription* → pick "Enterprise – yearly" template → move it Quotation → Confirmed → Active (actions menu). Try skipping a step: refused.
4. **Dashboard** → *Run billing (N due)*: hundreds of invoices created in seconds. Click again: **0 created**.
5. **Admin** → Discounts: show "Bulk seats 10%" (min 20 seats) and the dated, usage-limited "Diwali offer". Open a new invoice: the discount is on it.
6. Open an invoice → **Record payment** (part, then the rest) → status flips to **Paid**. Print it.
7. Invoices → filter **Overdue** → chase list. Dashboard filters by date and plan; the page shows its own load time.
8. Log in as the **customer**: they see only their subscriptions and invoices, and can pause or renew where the plan allows.
