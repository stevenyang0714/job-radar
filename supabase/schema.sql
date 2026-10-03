-- job-radar 資料表
-- 到 Supabase Dashboard → SQL Editor 貼上整份執行。可以重複執行，不會刪除既有的職缺資料。

-- 每一筆看過的職缺（不論是否通過篩選）都記錄在這裡，以 company + job_id 去重
create table if not exists public.jobs (
  id               bigint generated always as identity primary key,
  company          text not null,
  job_id           text not null,
  source           text not null,             -- sources.yaml 的 name
  ats              text not null,             -- workday / greenhouse / lever / ashby / mycareersfuture
  title            text not null,
  location         text,
  url              text not null,
  posted_date      date,
  description      text,
  salary           text,                      -- 例如「SGD 13,000–16,000 / 月」
  employment_type  text,
  via_agency       boolean not null default false,
  applicants       integer,
  first_seen_at    timestamptz not null default now(),
  last_seen_at     timestamptz not null default now(),
  closed_at        timestamptz,               -- 不再出現在網站上的時間；重新出現會清空
  filter_status    text,                      -- passed / rejected_location / rejected_exclude / rejected_description / rejected_keyword / duplicate
  score            smallint check (score between 0 and 100),
  score_detail     jsonb,                     -- AI 評分的完整結果（track、子分數、符合點、缺口…）
  scored_at        timestamptz,
  unique (company, job_id)
);

-- 步驟 1 建立的舊版資料表：補上新欄位，移除改由 notifications 與 score_detail 取代的欄位
alter table public.jobs add column if not exists salary text;
alter table public.jobs add column if not exists employment_type text;
alter table public.jobs add column if not exists via_agency boolean not null default false;
alter table public.jobs add column if not exists applicants integer;
alter table public.jobs add column if not exists closed_at timestamptz;
alter table public.jobs add column if not exists score_detail jsonb;
alter table public.jobs drop column if exists match_points;
alter table public.jobs drop column if exists main_gap;
alter table public.jobs drop column if exists notify_status;
alter table public.jobs drop column if exists notified_at;

create index if not exists jobs_source_idx on public.jobs (source);
create index if not exists jobs_unscored_idx on public.jobs (first_seen_at)
  where filter_status = 'passed' and score is null;

-- 推播紀錄：同一個職缺每種通知最多一次
create table if not exists public.notifications (
  id          bigint generated always as identity primary key,
  job         bigint not null references public.jobs (id) on delete cascade,
  kind        text not null,                    -- new（一週內新刊登）/ long_open（開了 4 個月以上）
  baseline    boolean not null default false,   -- 來源第一次執行時的初始盤點
  status      text not null default 'pending',  -- pending / sent / skipped
  created_at  timestamptz not null default now(),
  sent_at     timestamptz,
  unique (job, kind)
);

create index if not exists notifications_pending_idx on public.notifications (created_at)
  where status = 'pending';

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
  status      text not null,   -- ok / error / unavailable（例如網站維護）
  job_count   integer,
  new_count   integer,
  error       text,
  created_at  timestamptz not null default now()
);

create index if not exists source_runs_source_idx on public.source_runs (source, created_at desc);

-- 開啟 RLS 且不設任何 policy：只有 secret key（service_role）能讀寫，公開金鑰讀不到
alter table public.jobs          enable row level security;
alter table public.notifications enable row level security;
alter table public.runs          enable row level security;
alter table public.source_runs   enable row level security;

grant select, insert, update, delete on public.jobs, public.notifications, public.runs, public.source_runs to service_role;
