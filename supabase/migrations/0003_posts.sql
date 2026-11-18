-- =============================================================================
-- 0003_posts.sql
-- Đổi mô hình: 1 BÀI ĐĂNG = 1 NHÓM ẢNH (album) thuộc một phân loại
--  - Gom nhiều ảnh cùng loại vào 1 bài (ví dụ 10 ảnh ghế -> 1 bài về ghế)
--  - Ảnh có thể dùng lại sau N ngày (settings.reuse_days)
--  - Luân phiên kiểu đăng: album nhiều ảnh / 1 ảnh (settings.post_styles)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Bảng posts + post_photos
-- ---------------------------------------------------------------------------

create table if not exists posts (
  id uuid primary key default gen_random_uuid(),
  category_id uuid references categories(id) on delete set null,
  style text not null default 'album' check (style in ('album', 'single')),
  status text not null default 'draft' check (status in ('draft', 'ready', 'scheduled', 'posted', 'failed', 'skipped')),
  caption text,
  hashtags text[],
  ai_meta jsonb,
  scheduled_at timestamptz,
  posted_at timestamptz,
  fb_post_id text,
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists posts_status_idx on posts (status, scheduled_at);
create index if not exists posts_category_idx on posts (category_id, created_at desc);

create table if not exists post_photos (
  post_id uuid not null references posts(id) on delete cascade,
  photo_id uuid not null references photos(id) on delete cascade,
  position int not null default 0,
  primary key (post_id, photo_id)
);

create index if not exists post_photos_photo_idx on post_photos (photo_id);

-- ---------------------------------------------------------------------------
-- 2. Theo dõi việc dùng lại ảnh
-- ---------------------------------------------------------------------------

alter table photos add column if not exists used_at timestamptz;
alter table photos add column if not exists use_count int not null default 0;

create index if not exists photos_available_idx on photos (status, used_at);

-- ---------------------------------------------------------------------------
-- 3. Cấu hình mới
-- ---------------------------------------------------------------------------

alter table settings add column if not exists album_size int not null default 4;
alter table settings add column if not exists reuse_days int not null default 14;
alter table settings add column if not exists post_styles text[] not null default '{album,single}';
alter table settings add column if not exists max_posts_per_run int not null default 6;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'settings_album_size_check'
  ) then
    alter table settings add constraint settings_album_size_check check (album_size between 1 and 10);
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'settings_reuse_days_check'
  ) then
    alter table settings add constraint settings_reuse_days_check check (reuse_days between 0 and 365);
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'settings_max_posts_check'
  ) then
    alter table settings add constraint settings_max_posts_check check (max_posts_per_run between 1 and 20);
  end if;
end
$$;

-- ---------------------------------------------------------------------------
-- 4. RLS cho bảng mới
-- ---------------------------------------------------------------------------

alter table posts enable row level security;
alter table post_photos enable row level security;

drop policy if exists posts_member_all on posts;
create policy posts_member_all on posts
  for all to authenticated
  using (is_approved_member())
  with check (is_approved_member());

drop policy if exists post_photos_member_all on post_photos;
create policy post_photos_member_all on post_photos
  for all to authenticated
  using (is_approved_member())
  with check (is_approved_member());

-- ---------------------------------------------------------------------------
-- 5. Trigger updated_at
-- ---------------------------------------------------------------------------

drop trigger if exists posts_touch_updated_at on posts;
create trigger posts_touch_updated_at before update on posts
  for each row execute function touch_updated_at();

-- ---------------------------------------------------------------------------
-- 6. Hàm tiện ích cho worker: lấy ảnh đang dùng được (chưa dùng hoặc đã quá hạn dùng lại)
-- ---------------------------------------------------------------------------

create or replace function available_photos(p_reuse_days int default 14, p_limit int default 200)
returns setof photos
language sql
stable
security definer
set search_path = public
as $$
  select *
    from photos
   where status in ('pending', 'posted')
     and (
       used_at is null
       or used_at < now() - make_interval(days => greatest(0, p_reuse_days))
     )
   order by coalesce(used_at, to_timestamp(0)) asc, created_at asc
   limit p_limit;
$$;

revoke all on function available_photos(int, int) from public, anon, authenticated;
