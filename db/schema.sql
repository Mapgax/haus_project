CREATE TABLE IF NOT EXISTS public.seen_listings (
    fp TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    first_seen_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.listings_history (
    fp TEXT PRIMARY KEY,
    fetched_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_name TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    address TEXT NOT NULL,
    price_chf DOUBLE PRECISION,
    area_sqm DOUBLE PRECISION,
    rooms DOUBLE PRECISION,
    score INTEGER NOT NULL,
    raw_text TEXT NOT NULL,
    image_url TEXT,
    image_status TEXT, -- 'sold' | 'clear' | 'error' | NULL (OCR verdict on the title image)
    region TEXT NOT NULL DEFAULT 'ch', -- 'ch' | 'nl'
    currency TEXT NOT NULL DEFAULT 'CHF' -- price_chf holds this currency's amount
);

-- CREATE TABLE IF NOT EXISTS never adds columns to an existing table; these
-- idempotent ALTERs migrate the live database when --apply-schema runs.
ALTER TABLE public.listings_history ADD COLUMN IF NOT EXISTS image_url TEXT;
ALTER TABLE public.listings_history ADD COLUMN IF NOT EXISTS image_status TEXT;
ALTER TABLE public.listings_history ADD COLUMN IF NOT EXISTS region TEXT NOT NULL DEFAULT 'ch';
ALTER TABLE public.listings_history ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'CHF';

CREATE INDEX IF NOT EXISTS idx_listings_history_fetched_at
    ON public.listings_history (fetched_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_listings_history_category
    ON public.listings_history (category);

-- Personal values (distance reference point, price limits). The public repo and
-- the daily-committed assets/listings.json carry neutral defaults only; the
-- Vercel function api/listings.py overlays these at request time. One row, no
-- public policy: only the owner role and the read-only site role can see it
-- (see docs/private_settings.md for the role setup).
CREATE TABLE IF NOT EXISTS public.haus_private_settings (
    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.seen_listings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.listings_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.haus_private_settings ENABLE ROW LEVEL SECURITY;
