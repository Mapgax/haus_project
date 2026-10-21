# Supabase Scraping Setup

## Required Secret

Create one Supabase Postgres connection string and keep it out of git:

```bash
SUPABASE_DB_URL=postgresql://postgres.<project-ref>:<password>@<pooler-host>:6543/postgres?sslmode=require
```

Use the Supabase pooler connection string if possible. The direct connection
string also works, but the pooler is usually the better fit for scheduled jobs.

## RLS Model

`db/schema.sql` creates the two operational tables and enables row-level security:

- `public.seen_listings`
- `public.listings_history`

No public RLS policies are created. The GitHub Action writes through the private
Postgres connection string. The Vercel homepage continues to read the committed
static file `assets/listings.json`, so no browser-facing Supabase key is needed.

Do not put a Supabase `service_role` key or database password in frontend code.

## Local Migration

Fill `.env` locally, then run:

```bash
set -a
source .env
set +a
PYTHONPATH=src python3 scripts/migrate_sqlite_to_supabase.py
```

The migration is idempotent because both tables use `fp` as the primary key and
the inserts use `ON CONFLICT DO NOTHING`.

## GitHub Actions

Add a repository secret:

```text
SUPABASE_DB_URL
```

Then run the workflow manually once from GitHub Actions:

```text
Scrape property listings -> Run workflow
```

The schedule is `17:17 UTC` daily. In Zurich this is 19:17 during summer time
and 18:17 during winter time. GitHub Actions cron schedules are UTC.

## Vercel

Vercel should be connected to the GitHub repository. The Action commits only
`assets/listings.json` when the exported static data changes; Vercel deploys
that commit through the normal Git integration.
