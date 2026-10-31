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

## Billing and reports: Hours 2–5

Run `python manage.py migrate` after pulling these changes. Migration `0002` adds saved billing-run reports
and makes `(subscription, period_start)` unique even for cancelled invoices. If an older database already
contains duplicate cancelled periods, resolve those records before applying the constraint; this migration
does not delete financial history.

| Checkpoint | Runnable pages and behavior | Suggested commit |
|---|---|---|
| Hour 2 | `/invoices/`: number/customer search, status and inclusive invoice-date filters, 50 rows/page, detail links | `Add searchable paginated invoice list` |
| Hour 3 | Dashboard → Run billing → saved `/billing/runs/<id>/` report; run again to see 0; SMTP environment settings | `Add billing run reports and SMTP configuration` |
| Hour 4 | `/payments/`: method/payment-date filters and totals across all results; `/discounts/`: staff can read, admins create/edit | `Add payment reports and protected discount management` |
| Hour 5 | Dashboard revenue by plan and gross MRR; filtered CSV export; repeatable seed and page benchmark | `Add revenue reporting and verify 10k performance` |

Run `python manage.py test` before each commit, then push your commit to GitHub at the hourly checkpoint.
No commits or pushes are performed automatically. New reporting views live in `core/finance_views.py`;
the account app, catalog/subscription model classes and subscription/portal pages are unchanged.

**Definitions.** Outstanding = confirmed with due date today or later; Overdue = confirmed with a due date
before today, computed at request time. Date filters use `date_from` and `date_to`; dashboard retains `from`
and `to`. Revenue by plan is cash collected (including tax) in the selected period. MRR is today's gross
recurring plan fees plus subscription-line prices for active, started, unexpired subscriptions, before
discounts/tax. Monthly charges count once, annual charges divide by 12, weekly charges multiply by 52/12,
and daily charges multiply by 365/12. The plan filter affects MRR; historical date filters do not.

**Discount rules.** An eligible assigned discount has priority; otherwise the best eligible automatic discount
applies. Expiry is inclusive and evaluated on the billing execution date, including catch-up invoices.
Empty product scope includes the entire subscription; a selected scope discounts only those product lines.
Usage is locked and consumed in the invoice transaction, so concurrent PostgreSQL workers cannot exceed the
limit. Duplicate billing does not consume usage. Admin edits retain usage counters and existing invoices.

**Billing jobs.** The button starts a separate `process_billing_run` management-command process using the
same Python environment and database configuration. The result page polls until completion and paginates
created invoices. Worker output is in `.billing-worker.log` (ignored by Git). A failed job can be retried with
`python manage.py process_billing_run <id>`; after an interrupted running worker, start a new billing run.
The database period constraint and subscription locks prevent duplicate invoices. For cron or a manual
terminal demo, `python manage.py run_billing --details` runs synchronously and lists invoice/subscription
periods. Running it again produces 0; errors return a failing exit status and preserve affected billing dates.
This local subprocess worker is intended for the hackathon's persistent Django host.

**Invoice email.** The invoice detail page's Send action uses Django email settings. Console output remains
the default. For Mailtrap, set these variables in the shell that starts Django (copy actual credentials
from your Mailtrap inbox):

```powershell
$env:EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
$env:EMAIL_HOST = 'sandbox.smtp.mailtrap.io'
$env:EMAIL_PORT = '2525'
$env:EMAIL_HOST_USER = '<Mailtrap username>'
$env:EMAIL_HOST_PASSWORD = '<Mailtrap password>'
$env:EMAIL_USE_TLS = '1'
$env:EMAIL_USE_SSL = '0'
$env:DEFAULT_FROM_EMAIL = 'billing@brightloop.test'
```

`.env.example` documents the variables; this project does not automatically load `.env` files. SMTP errors
show a retryable message and do not mark the invoice sent. Automated tests use Django's in-memory outbox;
an actual Mailtrap delivery needs your credentials.

**10k verification.** On a fresh demo database:

```bash
python manage.py migrate
python manage.py seed_demo
python manage.py seed_demo          # safe no-op when subscriptions already exist
python manage.py run_billing --details
python manage.py run_billing        # 0 new invoices
python manage.py benchmark_pages    # 3 complete responses per route, budget 2 seconds
python manage.py test
```

Use `seed_demo --flush` only when you intend to replace existing demo data. The benchmark consumes the full
streamed CSV and rendered HTML; it reports query counts and worst response times, excluding browser rendering
and network latency. See `BILLING_HANDOFF.md` for measurements from the isolated local PostgreSQL demo.

## Demo script (5 minutes)

1. **Sign up** at `/signup/` with `weakpass`: rejected. Then use a strong password. Try the same email again: rejected.
2. **Admin** → Back office → Products: show *Brightloop CRM* with the **Odoo +560** variant, and Plans with the pausable/closable/renewable flags.
3. **Staff** → *New subscription* → pick "Enterprise – yearly" template → move it Quotation → Confirmed → Active (actions menu). Try skipping a step: refused.
4. **Dashboard** → *Run billing (N due)*: hundreds of invoices created in seconds. Click again: **0 created**.
5. **Admin** → Discounts: show "Bulk seats 10%" (min 20 seats) and the dated, usage-limited "Diwali offer". Open a new invoice: the discount is on it.
6. Open an invoice → **Record payment** (part, then the rest) → status flips to **Paid**. Print it.
7. Invoices → filter **Overdue** → chase list. Dashboard filters by date and plan; the page shows its own load time.
8. Log in as the **customer**: they see only their subscriptions and invoices, and can pause or renew where the plan allows.
