-- =============================================================================
-- 0001_init.sql — Khởi tạo schema cho Bot tự động đăng bài Facebook Page
-- Chạy trong Supabase SQL Editor (hoặc `supabase db push`).
-- Toàn bộ bảng đều bật Row Level Security (RLS) — chỉ chủ sở hữu đọc/ghi được.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Bảng
-- ---------------------------------------------------------------------------

do $$
begin
  if not exists (select 1 from pg_type where typname = 'photo_status') then
    create type photo_status as enum (
      'pending',      -- mới upload, chưa có caption
      'captioned',    -- đã có caption, chờ duyệt/xếp lịch
      'approved',     -- người dùng đã duyệt (nếu bật chế độ duyệt tay)
      'scheduled',    -- đã gửi lên Facebook với scheduled_publish_time
      'posted',       -- đã đăng xong
      'failed',       -- lỗi
      'skipped'       -- người dùng bỏ qua/xóa mềm
    );
  end if;
end
$$;

create table if not exists photos (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade default auth.uid(),
  storage_path text not null,                         -- đường dẫn trong bucket 'photos'
  status photo_status not null default 'pending',
  caption text,
  hashtags text[],
  ai_meta jsonb,                                      -- loại sản phẩm, độ tin cậy, ghi chú AI
  scheduled_at timestamptz,
  posted_at timestamptz,
  fb_post_id text,
  error_message text,
  attempts int not null default 0,
  -- Bổ sung so với đặc tả: chống trùng ảnh (SHA-256, mục 12) + theo dõi cập nhật
  content_hash text,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create index if not exists photos_owner_status_idx on photos (owner_id, status);
create index if not exists photos_scheduled_idx on photos (status, scheduled_at);
create unique index if not exists photos_owner_hash_idx on photos (owner_id, content_hash)
  where content_hash is not null and status <> 'skipped';

create table if not exists settings (
  owner_id uuid primary key references auth.users(id) on delete cascade default auth.uid(),
  posts_per_day int not null default 3 check (posts_per_day between 1 and 10),
  time_windows jsonb not null default '["09:00","12:00","20:00"]'::jsonb, -- giờ đăng (giờ VN)
  timezone text not null default 'Asia/Ho_Chi_Minh',
  jitter_minutes int not null default 10,             -- lệch ngẫu nhiên ± phút
  require_approval boolean not null default true,     -- mặc định BẬT duyệt tay
  tone text not null default 'thân thiện, tự nhiên, ngắn gọn',
  shop_info text not null default '',                 -- tên shop, địa chỉ, SĐT, chính sách...
  sample_posts text[] not null default '{}',          -- 2-3 bài mẫu để AI bắt chước giọng
  default_hashtags text[] not null default '{}',
  language text not null default 'vi',
  paused boolean not null default false,
  -- Bổ sung: cấu hình vận hành (đều có mặc định hợp lý)
  horizon_days int not null default 7 check (horizon_days between 1 and 29),
  retention_days int not null default 7 check (retention_days between 0 and 365),
  updated_at timestamptz not null default now()
);

create table if not exists post_logs (
  id bigserial primary key,
  photo_id uuid references photos(id) on delete set null,
  -- Bổ sung so với đặc tả: cần owner_id để RLS hoạt động trên bảng log
  owner_id uuid references auth.users(id) on delete cascade default auth.uid(),
  level text not null check (level in ('info', 'error', 'warn')),
  message text not null,
  detail jsonb,
  created_at timestamptz not null default now()
);

create index if not exists post_logs_owner_idx on post_logs (owner_id, created_at desc);

-- Khóa mềm chống 2 lần chạy worker chồng nhau (GitHub Actions cũng có `concurrency`)
create table if not exists worker_runs (
  id bigserial primary key,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  summary jsonb
);

-- ---------------------------------------------------------------------------
-- 2. Trigger cập nhật updated_at
-- ---------------------------------------------------------------------------

create or replace function touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end
$$;

drop trigger if exists photos_touch_updated_at on photos;
create trigger photos_touch_updated_at before update on photos
  for each row execute function touch_updated_at();

drop trigger if exists settings_touch_updated_at on settings;
create trigger settings_touch_updated_at before update on settings
  for each row execute function touch_updated_at();

-- ---------------------------------------------------------------------------
-- 3. Storage bucket private
-- ---------------------------------------------------------------------------

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('photos', 'photos', false, 10485760, array['image/jpeg', 'image/png', 'image/webp', 'image/heic'])
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- ---------------------------------------------------------------------------
-- 4. Row Level Security
-- ---------------------------------------------------------------------------

alter table photos       enable row level security;
alter table settings     enable row level security;
alter table post_logs    enable row level security;
alter table worker_runs  enable row level security;

-- photos: chỉ chủ sở hữu
drop policy if exists photos_owner_all on photos;
create policy photos_owner_all on photos
  for all to authenticated
  using (owner_id = auth.uid())
  with check (owner_id = auth.uid());

-- settings: chỉ chủ sở hữu
drop policy if exists settings_owner_all on settings;
create policy settings_owner_all on settings
  for all to authenticated
  using (owner_id = auth.uid())
  with check (owner_id = auth.uid());

-- post_logs: chỉ chủ sở hữu (ghi log do worker/service role thực hiện)
drop policy if exists post_logs_owner_select on post_logs;
create policy post_logs_owner_select on post_logs
  for select to authenticated
  using (owner_id = auth.uid());

drop policy if exists post_logs_owner_insert on post_logs;
create policy post_logs_owner_insert on post_logs
  for insert to authenticated
  with check (owner_id = auth.uid());

-- worker_runs: người dùng chỉ được xem
drop policy if exists worker_runs_owner_select on worker_runs;
create policy worker_runs_owner_select on worker_runs
  for select to authenticated
  using (true);

-- Storage: mỗi người chỉ đọc/ghi trong thư mục '<user_id>/...'
drop policy if exists photos_storage_select on storage.objects;
create policy photos_storage_select on storage.objects
  for select to authenticated
  using (bucket_id = 'photos' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists photos_storage_insert on storage.objects;
create policy photos_storage_insert on storage.objects
  for insert to authenticated
  with check (bucket_id = 'photos' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists photos_storage_update on storage.objects;
create policy photos_storage_update on storage.objects
  for update to authenticated
  using (bucket_id = 'photos' and (storage.foldername(name))[1] = auth.uid()::text)
  with check (bucket_id = 'photos' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists photos_storage_delete on storage.objects;
create policy photos_storage_delete on storage.objects
  for delete to authenticated
  using (bucket_id = 'photos' and (storage.foldername(name))[1] = auth.uid()::text);

-- ---------------------------------------------------------------------------
-- 5. Hàm tiện ích cho worker (service role)
-- ---------------------------------------------------------------------------

-- Lấy/cập nhật settings của chủ sở hữu duy nhất (app 1 người dùng).
create or replace function get_or_create_settings(p_owner uuid)
returns settings
language plpgsql
security definer
set search_path = public
as $$
declare
  s settings;
begin
  select * into s from settings where owner_id = p_owner;
  if not found then
    insert into settings (owner_id) values (p_owner) returning * into s;
  end if;
  return s;
end
$$;

revoke all on function get_or_create_settings(uuid) from public, anon, authenticated;

-- Claim nguyên tử: chỉ 1 tiến trình được lấy 1 ảnh để đăng (chống đăng trùng).
create or replace function claim_photo(p_id uuid)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  n int;
begin
  update photos
     set attempts = attempts + 1,
         updated_at = now()
   where id = p_id
     and status = 'approved'
     and fb_post_id is null;
  get diagnostics n = row_count;
  return n = 1;
end
$$;

revoke all on function claim_photo(uuid) from public, anon, authenticated;

-- Giữ project Supabase "thức" và dọn log cũ hơn 90 ngày.
create or replace function maintenance()
returns void
language sql
security definer
set search_path = public
as $$
  delete from post_logs where created_at < now() - interval '90 days';
$$;

revoke all on function maintenance() from public, anon, authenticated;
