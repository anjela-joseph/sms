# Catalog and subscriptions checkpoints

Run from the outer workspace directory in PowerShell (the application repository is the nested `sms/` directory):

```powershell
.venv/Scripts/python.exe sms/manage.py migrate
.venv/Scripts/python.exe sms/manage.py runserver
```

Set the PostgreSQL environment variables from README before running these commands to use PostgreSQL. Without them, the starter uses SQLite. No model migrations were added by these checkpoints.

## Hour 2 — subscription pages

Suggested commit: `feat: add subscription list and detail pages`

- `/subscriptions/`: number/customer search; status and plan filters; 50 rows per page, preserving filters.
- `/subscriptions/<id>/`: customer, plan, dates, payment terms, product/variant lines and recurring totals before discounts.
- Staff and admins can view; customers cannot. Related objects are fetched in bounded queries.

## Hour 3 — lifecycle actions

Suggested commit: `feat: add subscription lifecycle actions`

- POST-only, CSRF-protected buttons call `billing.change_status()` and `billing.renew()`; validation errors become messages.
- New subscription form rejects templates whose plan is not available today and redirects to the detail page.
- Pause/renew availability follows plan flags and current status.

## Hour 4 — catalog UI

Suggested commit: `feat: add catalog management pages`

- `/products/`, `/plans/`: paginated lists visible to staff/admins.
- `/products/new/`, `/products/<id>/edit/`, `/plans/new/`, `/plans/<id>/edit/`: admins only.
- Product forms support variants; save and reopen to add another blank variant row.
- Product, variant inline, and plan admin permissions also prevent staff writes.

## Hour 5 — polish and verification

Suggested commit: `style: polish catalog and subscription pages`

- Shared base styling, wrapping navigation, stacked mobile forms, horizontally scrolling tables, empty states, and cancel links.
- Verification: `.venv/Scripts/python.exe sms/manage.py test core accounts`.
- Manually check the new pages at desktop and 375px widths before presenting.

## Ownership and integration

New logic lives in `core/subscription_views.py`, `core/catalog_views.py`, and `core/catalog_forms.py` to minimize conflicts. Shared edits are URL registrations, base navigation/styles, the existing new-subscription form/view, and only Product/Plan/Variant admin classes.

No changes to accounts, billing, invoices, payments, discounts, or dashboard logic.

Totals on the subscription detail are explicitly before discounts; final invoice totals remain owned by billing. Ask P3 to expose a side-effect-free pricing preview if judges need discounted estimates here. The availability guard is on the new-subscription UI; ask P3 to also guard `billing.create_from_template()` if all service callers must enforce it.

Commit/push from the nested `sms/` repository using your team's branch workflow. Hours 2–4 were committed separately; the remaining polish and this guide are delivered together. The virtual environment is outside that repository.
