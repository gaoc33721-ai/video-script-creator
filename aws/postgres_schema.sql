create extension if not exists pgcrypto;
create extension if not exists vector;

create table if not exists product_feature_versions (
  id uuid primary key default gen_random_uuid(),
  file_name text not null,
  s3_key text,
  row_count integer not null default 0,
  model_count integer not null default 0,
  category_count integer not null default 0,
  created_by text,
  created_at timestamptz not null default now(),
  is_active boolean not null default false
);

create table if not exists product_features (
  id bigserial primary key,
  version_id uuid not null references product_feature_versions(id) on delete cascade,
  region text,
  brand text,
  category text,
  model text,
  language text,
  feature_name text,
  tagline text,
  feature_description text,
  created_at timestamptz not null default now()
);

create index if not exists idx_product_features_lookup
  on product_features (version_id, category, model, language);

create index if not exists idx_product_features_feature_name
  on product_features (feature_name);

create table if not exists competitor_assets (
  id uuid primary key default gen_random_uuid(),
  source_type text,
  platform text,
  channel text,
  platform_content_id text,
  account_handle text,
  account_url text,
  asin text,
  amazon_domain text,
  brand text,
  category text,
  title text,
  original_copy text,
  canonical_url text,
  source_url text,
  embed_url text,
  embed_html text,
  image_s3_key text,
  image_url text,
  thumbnail_expires_at timestamptz,
  engagement_snapshot jsonb not null default '{}'::jsonb,
  quality_score integer not null default 0,
  rights_status text not null default 'link_only_no_raw_video',
  review_status text not null default 'auto_collected',
  collected_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  ai_tags text[] not null default '{}',
  ai_analysis text,
  embedding vector(1536),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table competitor_assets add column if not exists platform_content_id text;
alter table competitor_assets add column if not exists account_handle text;
alter table competitor_assets add column if not exists account_url text;
alter table competitor_assets add column if not exists embed_url text;
alter table competitor_assets add column if not exists embed_html text;
alter table competitor_assets add column if not exists thumbnail_expires_at timestamptz;
alter table competitor_assets add column if not exists engagement_snapshot jsonb not null default '{}'::jsonb;

create index if not exists idx_competitor_assets_category
  on competitor_assets (category, brand, channel);

create index if not exists idx_competitor_assets_social_lookup
  on competitor_assets (platform, source_type, platform_content_id);

create unique index if not exists idx_competitor_assets_rainforest_asin
  on competitor_assets (source_type, amazon_domain, asin)
  where source_type = 'rainforest' and amazon_domain is not null and asin is not null;

create unique index if not exists idx_competitor_assets_platform_content
  on competitor_assets (platform, platform_content_id)
  where platform_content_id is not null and platform_content_id <> '';

create table if not exists competitor_asset_media (
  id uuid primary key default gen_random_uuid(),
  asset_id uuid not null references competitor_assets(id) on delete cascade,
  asin text,
  amazon_domain text,
  brand text,
  category text,
  product_url text,
  media_type text not null,
  media_url text not null,
  thumbnail_url text,
  thumbnail_expires_at timestamptz,
  title text,
  source_payload jsonb not null default '{}'::jsonb,
  rights_status text not null default 'link_only_no_raw_video',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table competitor_asset_media add column if not exists thumbnail_expires_at timestamptz;

create unique index if not exists idx_competitor_asset_media_url
  on competitor_asset_media (asset_id, media_type, media_url);

create index if not exists idx_competitor_asset_media_lookup
  on competitor_asset_media (asin, amazon_domain, media_type);

create table if not exists script_jobs (
  id uuid primary key default gen_random_uuid(),
  user_id text,
  category text,
  model text,
  platform text,
  market text,
  request_payload jsonb not null default '{}'::jsonb,
  status text not null default 'created',
  error_message text,
  created_at timestamptz not null default now()
);

create table if not exists script_variants (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references script_jobs(id) on delete cascade,
  variant_name text,
  label text,
  content text not null,
  export_s3_key text,
  created_at timestamptz not null default now()
);

create table if not exists script_feedback (
  id uuid primary key default gen_random_uuid(),
  job_id uuid references script_jobs(id) on delete set null,
  user_id text,
  score text,
  issues text[] not null default '{}',
  note text,
  created_at timestamptz not null default now()
);

create table if not exists competitor_configs (
  id uuid primary key default gen_random_uuid(),
  category text not null unique,
  config jsonb not null default '{}'::jsonb,
  updated_by text,
  updated_at timestamptz not null default now()
);
