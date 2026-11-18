-- =============================================================================
-- 0008_auto_classify.sql — AI tự phân loại ảnh
--
-- - photos.classified_at: đánh dấu ảnh đã được AI thử phân loại (thành công hay
--   không) để worker không gọi Gemini lặp lại mỗi lần chạy.
-- - settings.auto_classify: bật/tắt tự phân loại (mặc định bật).
-- - settings.classify_per_run: số ảnh tối đa phân loại mỗi lần chạy
--   (giới hạn để tiết kiệm quota Gemini free tier).
-- =============================================================================

alter table photos add column if not exists classified_at timestamptz;

alter table settings add column if not exists auto_classify boolean not null default true;
alter table settings add column if not exists classify_per_run int not null default 10;

do $$
begin
  alter table settings
    add constraint settings_classify_per_run_check
    check (classify_per_run between 1 and 50);
exception
  when duplicate_object then null;
end
$$;

-- Quét nhanh các ảnh chưa phân loại
create index if not exists photos_unclassified_idx
  on photos (created_at)
  where category_id is null and classified_at is null;

comment on column photos.classified_at is
  'Lần cuối AI thử phân loại ảnh (kể cả khi không khớp loại nào).';
comment on column settings.auto_classify is
  'Tự động dùng Gemini phân loại ảnh chưa có phân loại ở mỗi lần chạy worker.';
