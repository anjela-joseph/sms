# Brightloop billing handoff

Implementation directory: `C:\Users\hp\Desktop\sms\sms\brightloop`.
The Git root is its parent, `C:\Users\hp\Desktop\sms\sms`.

## Local verification setup

A project `.venv` contains Django 5.2.18 and psycopg 3.3.6. An isolated PostgreSQL 15 database is running
on `127.0.0.1:55432`, database/user `brightloop`. This local test instance uses trust authentication and
binds only to loopback. Its files, logs and the virtual environment are ignored by Git.
No existing PostgreSQL database was modified.

To use this same demo from PowerShell:

```powershell
Set-Location 'C:\Users\hp\Desktop\sms\sms\brightloop'
. .\.venv\Scripts\Activate.ps1
$env:POSTGRES_DB = 'brightloop'
$env:POSTGRES_USER = 'brightloop'
$env:POSTGRES_HOST = '127.0.0.1'
$env:POSTGRES_PORT = '55432'
python manage.py runserver
```

The README demo logins work. If the local database has stopped, restart it from this directory:

```powershell
& 'C:\Program Files\PostgreSQL\15\bin\pg_ctl.exe' -D .local-postgres -l .local-postgres.log -o '-p 55432 -h 127.0.0.1' start
```

Stop it when no longer needed:

```powershell
& 'C:\Program Files\PostgreSQL\15\bin\pg_ctl.exe' -D .local-postgres stop -m fast
```

For teammates' PostgreSQL instances, use their regular database environment settings and run migrations.

## Verified demo outcomes — 31 October 2026

- Seed: 10,000 subscriptions and 23,029 historical invoices; 14.5 seconds.
- Re-run seed: no data changed and no duplicate users or subscriptions.
- Real background billing run #1: 357 invoices, 1,173 due/expiring subscriptions processed, 0 errors;
  worker took 5.14 seconds, HTTP button request 32 ms, populated report 66 ms.
- Real background billing run #2: 0 invoices, 0 errors; HTTP button request 14 ms.
- Resulting dataset: 10,000 subscriptions and 23,386 invoices.
- Full suite: 36 tests passed on PostgreSQL, including real parallel-connection discount/billing tests.
- SQLite fallback: suite passed with the PostgreSQL row-lock concurrency test skipped.
- Read-only audit of all 23,386 invoices: 0 duplicate periods, 0 period gaps, 0 incorrect billing cursors,
  and 0 mismatches between invoice totals and their line totals.
- Account files and catalog/subscription model class bodies were compared with the supplied ZIP and are unchanged.

Full-response performance includes template rendering, SQL and consuming CSV streams, not browser/network
time. The repeatable `benchmark_pages` command checks 22 routes three times each and fails if any run reaches
2 seconds. Billing processing itself runs outside the HTTP response.

Latest benchmark (worst of three requests, after optimizing CSV to fetch only required columns):

| Route | Worst time | SQL queries |
|---|---:|---:|
| Dashboard | 97 ms | 13 |
| Dashboard, plan filter | 105 ms | 13 |
| Invoice list | 27 ms | 4 |
| Invoice search + Paid + page 20 | 94 ms | 4 |
| Invoice detail | 12 ms | 5 |
| Full invoice CSV, 23,386 rows | 302 ms | 3 |
| Payments | 28 ms | 5 |
| Discounts list | 7 ms | 5 |
| Discount create/edit | 9 ms | 5 / 7 |
| Billing result (second run) | 21 ms | 3 |
| Existing new-subscription page (unchanged) | 569 ms | 4 |
| Existing customer portal (unchanged) | 62 ms | 8 |

All 22 measured routes passed. Timings vary by host load; rerun the command on the hackathon server.

## Team integration

The complete billing/reporting implementation is integrated with the team's account, catalog and subscription
pages. The publishing checkout is `C:\Users\hp\Desktop\sms\hour2-publish`; the earlier implementation
directory above holds the original local demo environment. The project's files are at the GitHub repository
root. Run `python manage.py migrate` after pulling, and `python manage.py test` before future commits.

Final integrated validation: all 59 tests passed on PostgreSQL, no missing migrations, and all 22 benchmark
routes passed three runs each on 10,000 subscriptions / 23,386 invoices. The slowest full response was the
complete CSV export at 645 ms; dashboard response time was 158 ms. These measurements exclude browser
rendering and network latency.
