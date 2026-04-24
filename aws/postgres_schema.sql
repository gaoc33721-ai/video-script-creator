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
  channel text,
  brand text,
  category text,
  title text,
  original_copy text,
  source_url text,
  image_s3_key text,
  metadata jsonb not null default '{}'::jsonb,
  ai_tags text[] not null default '{}',
  ai_analysis text,
  embedding vector(1536),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_competitor_assets_category
  on competitor_assets (category, brand, channel);

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
