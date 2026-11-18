-- =============================================================================
-- 0007_fix_profiles_policy.sql
-- Sửa lỗi "infinite recursion detected in policy for relation profiles":
-- policy cũ tự truy vấn bảng profiles -> đệ quy. Dùng hàm security definer.
-- =============================================================================

create or replace function is_owner()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from profiles
     where id = auth.uid()
       and role = 'owner'
       and approved
  );
$$;

grant execute on function is_owner() to authenticated;

drop policy if exists profiles_owner_all on profiles;
create policy profiles_owner_all on profiles
  for all to authenticated
  using (is_owner())
  with check (is_owner());
