-- =============================================================================
-- 0002_accounts_categories.sql
-- 1) Tài khoản: bảng profiles + mã mời (đăng ký có kiểm soát)
-- 2) Đổi mật khẩu: dùng Supabase Auth (updateUser) — không cần bảng
-- 3) Phân loại mặt hàng: bảng categories + photos.category_id
-- 4) Đăng bài theo phân loại: settings.category_rotation (worker luân phiên)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Profiles (thành viên được phép dùng app)
-- ---------------------------------------------------------------------------

create table if not exists profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  display_name text,
  role text not null default 'staff' check (role in ('owner', 'staff')),
  approved boolean not null default false,
  created_at timestamptz not null default now()
);

alter table profiles enable row level security;

-- Mỗi người chỉ đọc được profile của chính mình
drop policy if exists profiles_self_select on profiles;
create policy profiles_self_select on profiles
  for select to authenticated
  using (id = auth.uid());

-- Chỉ chủ shop (owner) xem/quản lý danh sách thành viên
drop policy if exists profiles_owner_all on profiles;
create policy profiles_owner_all on profiles
  for all to authenticated
  using (exists (select 1 from profiles p where p.id = auth.uid() and p.role = 'owner'))
  with check (exists (select 1 from profiles p where p.id = auth.uid() and p.role = 'owner'));

-- Hàm kiểm tra "thành viên đã được duyệt" (dùng trong RLS, bỏ qua RLS)
create or replace function is_approved_member()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (select 1 from profiles where id = auth.uid() and approved);
$$;

grant execute on function is_approved_member() to authenticated;

-- Tự tạo profile khi có người đăng ký (mặc định CHƯA duyệt)
create or replace function handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email, display_name)
  values (
    new.id,
    new.email,
    coalesce(nullif(new.raw_user_meta_data ->> 'display_name', ''), split_part(new.email, '@', 1))
  )
  on conflict (id) do nothing;
  return new;
end
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function handle_new_user();

-- ---------------------------------------------------------------------------
-- 2. Mã mời (đăng ký công khai nhưng phải có mã mới dùng được app)
-- ---------------------------------------------------------------------------

create table if not exists app_config (
  key text primary key,
  value text not null,
  updated_at timestamptz not null default now()
);

alter table app_config enable row level security;
-- Không tạo policy nào: anon/authenticated KHÔNG đọc được, chỉ service role.

insert into app_config (key, value)
values ('invite_code', 'MUABAN2026')
on conflict (key) do nothing;

-- Kích hoạt tài khoản bằng mã mời. Người đầu tiên được duyệt trở thành owner.
create or replace function redeem_invite(p_code text)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_ok boolean;
  v_owner_exists boolean;
begin
  if auth.uid() is null then
    return false;
  end if;

  select (lower(trim(value)) = lower(trim(coalesce(p_code, ''))))
    into v_ok
    from app_config
   where key = 'invite_code';

  if not coalesce(v_ok, false) then
    return false;
  end if;

  select exists (select 1 from profiles where role = 'owner') into v_owner_exists;

  update profiles
     set approved = true,
         role = case when v_owner_exists then role else 'owner' end
   where id = auth.uid();

  return true;
end
$$;

revoke all on function redeem_invite(text) from public, anon;
grant execute on function redeem_invite(text) to authenticated;

-- Backfill: người dùng đang có (chủ shop) -> owner + đã duyệt
insert into profiles (id, email, display_name, role, approved)
select u.id,
       u.email,
       coalesce(nullif(u.raw_user_meta_data ->> 'display_name', ''), split_part(u.email, '@', 1)),
       'owner',
       true
  from auth.users u
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- 3. Phân loại mặt hàng
-- ---------------------------------------------------------------------------

create table if not exists categories (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  hint text not null default '',                      -- ghi chú cho AI (chất liệu, tình trạng, cách gọi...)
  hashtags text[] not null default '{}',
  active boolean not null default true,
  sort_order int not null default 0,
  created_at timestamptz not null default now()
);

create unique index if not exists categories_name_uidx on categories (lower(name));

alter table categories enable row level security;

drop policy if exists categories_member_all on categories;
create policy categories_member_all on categories
  for all to authenticated
  using (is_approved_member())
  with check (is_approved_member());

-- Ảnh thuộc về một phân loại (xóa loại thì ảnh vẫn còn, chỉ mất nhãn)
alter table photos add column if not exists category_id uuid references categories(id) on delete set null;
create index if not exists photos_category_idx on photos (category_id, status, created_at);

-- ---------------------------------------------------------------------------
-- 4. Cấu hình: luân phiên phân loại khi xếp lịch
-- ---------------------------------------------------------------------------

alter table settings add column if not exists category_rotation boolean not null default true;

-- ---------------------------------------------------------------------------
-- 5. RLS: chuyển từ "chỉ chủ sở hữu" sang "mọi thành viên đã duyệt"
-- ---------------------------------------------------------------------------

drop policy if exists photos_owner_all on photos;
create policy photos_member_all on photos
  for all to authenticated
  using (is_approved_member())
  with check (is_approved_member());

drop policy if exists settings_owner_all on settings;
create policy settings_member_all on settings
  for all to authenticated
  using (is_approved_member())
  with check (is_approved_member());

drop policy if exists post_logs_owner_select on post_logs;
create policy post_logs_member_select on post_logs
  for select to authenticated
  using (is_approved_member());

drop policy if exists post_logs_owner_insert on post_logs;
create policy post_logs_member_insert on post_logs
  for insert to authenticated
  with check (is_approved_member());

-- Storage: thành viên xem được mọi ảnh của shop, nhưng chỉ ghi trong thư mục của mình
drop policy if exists photos_storage_select on storage.objects;
create policy photos_storage_select on storage.objects
  for select to authenticated
  using (bucket_id = 'photos' and is_approved_member());

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
  using (bucket_id = 'photos' and is_approved_member());

-- ---------------------------------------------------------------------------
-- 6. Trigger updated_at cho bảng mới
-- ---------------------------------------------------------------------------

drop trigger if exists categories_touch_updated_at on categories;
create trigger categories_touch_updated_at before update on categories
  for each row execute function touch_updated_at();

drop trigger if exists app_config_touch_updated_at on app_config;
create trigger app_config_touch_updated_at before update on app_config
  for each row execute function touch_updated_at();

-- ---------------------------------------------------------------------------
-- 7. Dữ liệu mẫu: vài phân loại hay dùng cho shop đồ cũ
-- ---------------------------------------------------------------------------

insert into categories (name, hint, hashtags, sort_order)
values
  ('Ghế văn phòng', 'Ghế xoay, ghế lưới, ghế da, ghế chân quỳ; hàng đã qua sử dụng, nhiều chiếc còn đẹp',
   array['ghevanphong', 'ghethanhly', 'vanphong'], 1),
  ('Bàn làm việc', 'Bàn chữ L, bàn thẳng, bàn họp; kích thước đa dạng, có thể tháo lắp',
   array['banvanphong', 'banlamviec', 'thanhly'], 2),
  ('Tủ & kệ', 'Tủ tài liệu, tủ locker, kệ sách; còn sử dụng tốt',
   array['tuhoso', 'keluuho', 'vanphong'], 3)
on conflict do nothing;
