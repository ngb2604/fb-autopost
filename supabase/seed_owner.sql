-- =============================================================================
-- seed_owner.sql — Điền sẵn cài đặt cho chủ shop (chạy SAU khi đã tạo user).
-- Idempotent: chạy lại nhiều lần không sao.
-- =============================================================================

do $seed$
declare
  v_owner uuid;
  v_shop text;
  v_post1 text;
  v_post2 text;
begin
  select id into v_owner from auth.users order by created_at asc limit 1;
  if v_owner is null then
    raise notice 'Chua co user nao trong auth.users - hay tao user truoc.';
    return;
  end if;

  v_shop := $txt$Page: Mua Bán - Thanh Lý Đồ Cũ Tại Hà Nội
Kho: Ngõ 300 Nguyễn Xiển, Hà Nội (đi vào ngõ 70m bên tay phải)
Zalo/Hotline: 0973807223 — hỗ trợ gọi xe chở tận nơi
Hàng đã qua sử dụng, khách đến kho xem và chọn trực tiếp; mua lẻ 1-2 chiếc hoặc mua số lượng cho công ty, văn phòng đều được.
Chưa có chính sách đổi trả / bảo hành chính thức — nếu khách hỏi, mời khách nhắn tin để trao đổi cụ thể.$txt$;

  v_post1 := $txt$XẢ KHO GHẾ VĂN PHÒNG – GIÁ THANH LÝ
Kho đang có nhiều loại ghế văn phòng: ghế xoay, ghế lưới, ghế da, ghế chân quỳ… nhiều mẫu, nhiều mức giá.
Hàng đã qua sử dụng, nhiều chiếc còn đẹp.
Có thể đến kho xem và chọn trực tiếp.
Phù hợp mua lẻ, mua số lượng cho công ty, văn phòng.
Ngõ 300 Nguyễn Xiển (vào ngõ 70m bên tay phải)
Zalo/Hotline: 0973807223
Cần ghế cứ nhắn tin, gửi mẫu và báo giá nhanh nhé!$txt$;

  v_post2 := $txt$TỔNG XẢ KHO LÔ GHẾ VĂN PHÒNG - GIÁ CHỈ TỪ VÀI BÁT PHỞ!
Bác nào đang setup văn phòng, trung tâm mà muốn tiết kiệm chi phí thì bơi hết vào đây!
Kho em đang xả gấp lô ghế thanh lý nhưng độ mới cực cao. Đệm mút êm ru, ben hơi nâng hạ mượt mà, khung bánh xe chắc nịch.
Mua lẻ 1-2 chiếc hay ôm sỉ cả lô em đều để giá sập sàn. Hàng ngon giá bèo bay rất nhanh, bác nào chốt sớm chấm nhẹ dưới bài em báo giá yêu thương ngay nhé!
Kho: Ngõ 300 Nguyễn Xiển, Hà Nội (đi vào ngõ 70m bên tay phải)
Zalo/Alo: 0973807223 - Hỗ trợ gọi xe chở tận nơi!$txt$;

  insert into settings (
    owner_id, posts_per_day, time_windows, jitter_minutes, require_approval,
    tone, shop_info, sample_posts, default_hashtags, retention_days, paused
  )
  values (
    v_owner, 3, '["09:00","12:00","20:00"]'::jsonb, 10, false,
    'thân thiện, tự nhiên, ngắn gọn, kiểu người bán hàng thật thà',
    v_shop, array[v_post1, v_post2],
    array['#thanhlydocu', '#dogocu', '#vanphong', '#hanoi'],
    7, false
  )
  on conflict (owner_id) do update set
    posts_per_day = excluded.posts_per_day,
    time_windows = excluded.time_windows,
    jitter_minutes = excluded.jitter_minutes,
    require_approval = excluded.require_approval,
    tone = excluded.tone,
    shop_info = excluded.shop_info,
    sample_posts = excluded.sample_posts,
    default_hashtags = excluded.default_hashtags,
    retention_days = excluded.retention_days,
    paused = excluded.paused,
    updated_at = now();

  raise notice 'Da seed settings cho owner %', v_owner;
end
$seed$;
