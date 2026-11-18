-- =============================================================================
-- 0005_photo_reserve.sql
-- Giữ chỗ ảnh khi tạo bài (tránh 2 lần chạy dùng cùng ảnh) + cấp quyền cho
-- service_role gọi các hàm nội bộ của worker.
-- =============================================================================

create or replace function reserve_photos(p_ids uuid[])
returns void
language sql
security definer
set search_path = public
as $$
  update photos
     set status = 'scheduled',
         updated_at = now()
   where id = any(p_ids)
     and status in ('pending', 'posted');
$$;

revoke all on function reserve_photos(uuid[]) from public, anon, authenticated;
grant execute on function reserve_photos(uuid[]) to service_role;

-- service_role được gọi mọi hàm helper của worker
grant execute on function available_photos(int, int) to service_role;
grant execute on function claim_post(uuid) to service_role;
grant execute on function mark_photos_used(uuid[], timestamptz) to service_role;
grant execute on function mark_photos_posted(uuid[]) to service_role;
grant execute on function release_photos(uuid[]) to service_role;
