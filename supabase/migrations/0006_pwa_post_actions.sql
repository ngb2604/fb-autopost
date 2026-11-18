-- =============================================================================
-- 0006_pwa_post_actions.sql
-- Cho PWA duyệt / bỏ qua bài đăng (chỉ thành viên đã duyệt).
-- =============================================================================

create or replace function pwa_set_post_status(p_id uuid, p_status text)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_photo_ids uuid[];
begin
  if auth.uid() is null or not is_approved_member() then
    return false;
  end if;

  if p_status not in ('ready', 'skipped') then
    return false;
  end if;

  select coalesce(array_agg(photo_id), '{}')
    into v_photo_ids
    from post_photos
   where post_id = p_id;

  if p_status = 'ready' then
    update posts
       set status = 'ready',
           scheduled_at = null,
           fb_post_id = null,
           error_message = null
     where id = p_id
       and status in ('draft', 'failed', 'skipped', 'scheduled');
    perform reserve_photos(v_photo_ids);
  else
    update posts
       set status = 'skipped',
           error_message = 'Bỏ qua từ app'
     where id = p_id
       and status in ('draft', 'ready', 'failed', 'scheduled');
    perform release_photos(v_photo_ids);
  end if;

  return true;
end
$$;

revoke all on function pwa_set_post_status(uuid, text) from public, anon;
grant execute on function pwa_set_post_status(uuid, text) to authenticated;
