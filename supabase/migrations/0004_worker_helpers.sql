-- =============================================================================
-- 0004_worker_helpers.sql
-- Trạng thái 'scheduling' + các hàm nguyên tử cho worker khi đăng album
-- =============================================================================

alter table posts drop constraint if exists posts_status_check;
alter table posts add constraint posts_status_check
  check (status in ('draft', 'ready', 'scheduling', 'scheduled', 'posted', 'failed', 'skipped'));

-- Giữ chỗ một bài trước khi gọi Facebook (chống 2 lần chạy lấy cùng 1 bài)
create or replace function claim_post(p_id uuid)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  n int;
begin
  update posts
     set status = 'scheduling',
         updated_at = now()
   where id = p_id
     and status = 'ready'
     and fb_post_id is null;
  get diagnostics n = row_count;
  return n = 1;
end
$$;

revoke all on function claim_post(uuid) from public, anon, authenticated;

-- Đánh dấu các ảnh vừa được dùng trong một bài
create or replace function mark_photos_used(p_ids uuid[], p_at timestamptz default now())
returns void
language sql
security definer
set search_path = public
as $$
  update photos
     set status = 'scheduled',
         used_at = p_at,
         use_count = use_count + 1,
         updated_at = now()
   where id = any(p_ids);
$$;

revoke all on function mark_photos_used(uuid[], timestamptz) from public, anon, authenticated;

-- Khi bài đăng xong: ảnh chuyển sang 'posted' (vẫn dùng lại được sau reuse_days)
create or replace function mark_photos_posted(p_ids uuid[])
returns void
language sql
security definer
set search_path = public
as $$
  update photos
     set status = 'posted',
         updated_at = now()
   where id = any(p_ids)
     and status <> 'skipped';
$$;

revoke all on function mark_photos_posted(uuid[]) from public, anon, authenticated;

-- Trả ảnh về trạng thái dùng được khi bài lỗi
create or replace function release_photos(p_ids uuid[])
returns void
language sql
security definer
set search_path = public
as $$
  update photos
     set status = 'pending',
         used_at = null,
         use_count = greatest(0, use_count - 1),
         updated_at = now()
   where id = any(p_ids)
     and status = 'scheduled';
$$;

revoke all on function release_photos(uuid[]) from public, anon, authenticated;
