-- job-radar 資料表
-- 到 Supabase Dashboard → SQL Editor 貼上整份執行。可以重複執行，不會刪除既有資料。

-- 每一筆看過的職缺（不論是否通過篩選）都記錄在這裡，以 company + job_id 去重
create table if not exists public.jobs (
  id             bigint generated always as identity primary key,
  company        text not null,
  job_id         text not null,
  source         text not null,             -- sources.yaml 的 name
  ats            text not null,             -- workday / greenhouse / lever / ashby / generic
  title          text not null,
  location       text,
  url            text not null,
  posted_date    date,
  description    text,
  first_seen_at  timestamptz not null default now(),
  last_seen_at   timestamptz not null default now(),
  filter_status  text,                      -- passed / rejected_location / rejected_exclude / rejected_keyword
  score          smallint check (score between 0 and 100),
  match_points   jsonb,                     -- 3 個符合點
  main_gap       text,
  scored_at      timestamptz,
  notify_status  text,                      -- pending_instant / pending_digest / pending_baseline / sent / skipped
  notified_at    timestamptz,
  unique (company, job_id)
);

create index if not exists jobs_unscored_idx on public.jobs (first_seen_at)
  where filter_status = 'passed' and score is null;
create index if not exists jobs_pending_notify_idx on public.jobs (notify_status)
  where notify_status like 'pending%';

-- 每次執行一筆
create table if not exists public.runs (
  id           bigint generated always as identity primary key,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  trigger      text not null,                    -- schedule / manual / local
  status       text not null default 'running',  -- running / ok / partial / failed
  summary      jsonb
);

-- 每次執行中每個來源一筆，用來偵測抓取失敗與「突然 0 筆」
create table if not exists public.source_runs (
  id          bigint generated always as identity primary key,
  run_id      bigint not null references public.runs (id) on delete cascade,
  source      text not null,
  status      text not null,   -- ok / error
  job_count   integer,
  new_count   integer,
  error       text,
  created_at  timestamptz not null default now()
);

create index if not exists source_runs_source_idx on public.source_runs (source, created_at desc);

-- 開啟 RLS 且不設任何 policy：只有 secret key（service_role）能讀寫，公開金鑰讀不到
alter table public.jobs        enable row level security;
alter table public.runs        enable row level security;
alter table public.source_runs enable row level security;

grant select, insert, update, delete on public.jobs, public.runs, public.source_runs to service_role;
