# Private settings (distance reference point, price limits)

The repository is public and so is the daily-committed `assets/listings.json`.
Both carry **neutral defaults only** (town-centre reference point, CHF 3 Mio. /
CHF 5'000). Personal values live in the database and are applied per request by
a Vercel function behind the project's login protection.

```
browser -> /api/listings (Vercel function, SSO-protected)
              |- reads assets/listings.json        (public data, bundled)
              |- reads public.haus_private_settings (private, read-only role)
              '- returns the data with the personal values overlaid
```

If anything is missing or fails (no settings, database down, bad values) the
function returns the public data unchanged, and `assets/app.js` falls back to the
static file if the function itself is unavailable. Nothing breaks without it.

## What can be set

| Key | Effect |
|---|---|
| `ref_ch` (`label`, `lat`, `lon`) | Swiss distance reference point; every Swiss listing's `distanceKm` is recomputed with the exporter's formula |
| `buy_limit_chf`, `rent_limit_chf` | Default price filter and its label (`CHF 4.5 Mio.`) |

The Dutch reference point and criteria are not personal and stay as exported.

## Setting values (from your Mac, never from CI)

```bash
set -a && source .env && set +a        # needs SUPABASE_DB_URL (owner role)
python3 scripts/set_private_settings.py \
    --ref-ch-label "..." --ref-ch-lat 47.50 --ref-ch-lon 9.10 \
    --buy-limit-chf 2500000 --rent-limit-chf 4000
python3 scripts/set_private_settings.py --show            # which keys are set
python3 scripts/set_private_settings.py --clear           # back to public defaults
```

**Never set `HAUS_REF_*` / `HAUS_*_LIMIT_CHF` in GitHub Actions.** The Action
commits its output, so anything set there would be published.

## One-time setup

1. **Table.** `db/schema.sql` creates `public.haus_private_settings` (RLS on, no
   policy) on the next `--apply-schema` run, or run the setter script once.
2. **Read-only role for the website.** The function must not hold the owner
   password. In the Supabase SQL editor (replace the password placeholder):

   ```sql
   CREATE ROLE haus_site_ro LOGIN PASSWORD '<generate a long random password>';
   GRANT USAGE ON SCHEMA public TO haus_site_ro;
   GRANT SELECT ON public.haus_private_settings TO haus_site_ro;
   CREATE POLICY haus_site_ro_read ON public.haus_private_settings
       FOR SELECT TO haus_site_ro USING (true);
   ```

3. **Vercel environment variable** `HAUS_SETTINGS_DB_URL` (Production and
   Preview): the pooler connection string for that role, in the same form as
   `SUPABASE_DB_URL` but with user `haus_site_ro.<project-ref>`. It is
   server-side only; the browser never sees it.
4. **Keep Deployment Protection on** (Vercel project settings, Vercel
   Authentication for *all* deployments). The overlay response is only as
   private as the site itself.

## Rotating the site role's password

`ALTER ROLE haus_site_ro PASSWORD '...'`, then update `HAUS_SETTINGS_DB_URL` in
Vercel and redeploy. The Supabase pooler picks up a changed password with a short
delay.
