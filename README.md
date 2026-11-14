# Auto Post — Bot đăng bài Facebook Page (PWA + Supabase + GitHub Actions)

Tự động viết caption tiếng Việt bằng AI và **lên lịch đăng** ảnh sản phẩm lên Facebook Page.
Toàn bộ chạy trong **gói miễn phí**: GitHub Pages (PWA) + Supabase (DB/Storage/Auth) +
GitHub Actions (cron) + Gemini API + Facebook Graph API + Telegram.

```
iPhone (PWA)  ──upload ảnh đã nén──▶  Supabase (Auth + Postgres + Storage private)
                                            ▲
                                            │ service role
                              GitHub Actions ─┴─ worker.py (cron 08:00 mỗi ngày)
                                   │                    │
                          Gemini API (viết caption)   Facebook Graph API (lên lịch)
                                   │
                              Telegram Bot (báo kết quả/lỗi)
```

---

## Tính năng

- 📷 Thêm nhiều ảnh từ iPhone, **nén ngay trên máy** (≤1600px, JPEG 82%), chống trùng bằng SHA-256.
- 🤖 Gemini đọc ảnh → caption tiếng Việt + hashtag (JSON schema, không bịa giá/thông số).
- 🗓️ Tự chia khung giờ theo số bài/ngày (1–10), có jitter ngẫu nhiên, đúng múi giờ VN.
- ✅ Chế độ **duyệt tay** (mặc định BẬT) — bài chưa duyệt không bao giờ lên Facebook.
- 🔔 Báo Telegram: tổng kết mỗi lần chạy, lỗi token, sắp hết ảnh.
- 🔁 Chống đăng trùng (claim nguyên tử trong DB) + `concurrency` của GitHub Actions.
- 🌙 Giao diện tiếng Việt, một cột, nút lớn, dark mode, cài được vào Màn hình chính.

---

## Cấu trúc

```
web/                      PWA (deploy GitHub Pages)
  index.html app.js style.css manifest.json sw.js config.js icons/
worker/                   Worker Python
  main.py caption.py scheduler.py facebook.py db.py notify.py config.py
  facebook_test.py caption_test.py requirements.txt tests/
supabase/migrations/0001_init.sql
docs/SETUP_USER.md        Hướng dẫn từng bước cho chủ shop
scripts/make_icons.py     Tạo icon PWA
.github/workflows/        worker.yml (cron) + pages.yml (deploy PWA)
```

---

## Cài đặt nhanh (cho người đã quen)

1. **Supabase**: tạo project → chạy `supabase/migrations/0001_init.sql` trong SQL Editor →
   tạo 1 user → **tắt signup** → lấy `URL`, `anon key`, `service_role key`.
2. **Gemini**: lấy API key ở <https://aistudio.google.com/apikey>.
3. **Meta**: tạo app Business (Development) → quyền `pages_manage_posts`,
   `pages_read_engagement`, `pages_show_list` → lấy `FB_PAGE_ID` + Page Access Token dài hạn.
4. **Telegram**: `@BotFather` → `/newbot` → lấy token + chat id.
5. **GitHub**: tạo repo → dán 7 secret → bật Pages (Source: GitHub Actions).
6. Sửa `web/config.js`:
   ```js
   window.APP_CONFIG = {
     SUPABASE_URL: "https://xxxx.supabase.co",
     SUPABASE_ANON_KEY: "eyJhbGciOi...",
   };
   ```
7. Mở link Pages bằng Safari trên iPhone → **Thêm vào MH chính** → đăng nhập.

Chi tiết từng bước: **[docs/SETUP_USER.md](docs/SETUP_USER.md)**

---

## Chạy thử trên máy

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r worker/requirements.txt pytest

cp .env.example .env      # rồi điền giá trị thật (file .env đã bị .gitignore)

python -m pytest worker/tests -q          # unit test (không cần mạng)

# M1: đăng thử 1 ảnh lên Page
python -m worker.facebook_test anh.jpg "Caption thử" --delete

# M2: thử Gemini với 1 ảnh
python -m worker.caption_test anh.jpg --shop "Shop ABC, 0912..."

# M4: xem kế hoạch, KHÔNG gọi Facebook
python -m worker.main --dry-run

# Chạy thật (xếp lịch lên Facebook)
python -m worker.main
```

---

## Đưa code lên GitHub

```bash
git init
git add .
git commit -m "feat: bot đăng bài Facebook Page (PWA + worker)"
git branch -M main
git remote add origin https://github.com/<username>/fb-autopost.git
git push -u origin main
```

> Repo **Private** là tốt nhất. GitHub Pages cho repo private cần gói trả phí;
> nếu dùng gói Free, để repo **Public** — code không chứa secret (tất cả nằm trong GitHub Secrets).

---

## Vận hành hằng ngày

| Việc | Ở đâu |
|---|---|
| Thêm ảnh | App → tab **Kho ảnh** |
| Duyệt / sửa caption | App → tab **Hàng đợi** |
| Đổi số bài, giờ đăng, giọng văn | App → tab **Cài đặt** |
| Xem bài đã đăng, lỗi | App → tab **Lịch sử** |
| Chạy worker ngay | GitHub → Actions → **Worker đăng bài** → **Run workflow** |
| Dừng khẩn cấp | App → Cài đặt → **Tạm dừng toàn bộ** |

Worker chạy tự động **08:00 sáng mỗi ngày** (giờ VN). Có thể đổi cron trong
`.github/workflows/worker.yml` (`cron: "0 1 * * *"` = 01:00 UTC).

---

## Giới hạn gói miễn phí & cách xử lý

| Dịch vụ | Giới hạn | Khi chạm giới hạn |
|---|---|---|
| **Supabase Free** | 500MB DB, 1GB Storage; project **tự tạm dừng** nếu ~1 tuần không hoạt động | Worker chạy hằng ngày giúp giữ "thức". Nếu bị pause: dashboard → **Restore project**. Bật `retention_days` để tự xoá ảnh cũ. |
| **Gemini free tier** | Giới hạn lượt/phút và lượt/ngày, không công bố cố định | Worker chạy tuần tự, có backoff khi 429; giảm `MAX_PHOTOS_PER_RUN` (mặc định 5). |
| **GitHub Actions** | 2.000 phút/tháng (private), không giới hạn (public) | Job chỉ chạy ~1 phút/ngày, có cache pip. |
| **Facebook Graph API** | App ở Development mode, chỉ 1 người dùng | Không cần App Review. Token Page ~60 ngày → làm mới định kỳ. |
| **GitHub cron** | Có thể trễ vài phút; tự tắt sau 60 ngày repo không hoạt động | Bật lại: Actions → Worker → **Enable workflow**. |

---

## Thiết kế an toàn

- **Không đăng trùng**: hàm SQL `claim_photo()` chỉ cho một tiến trình "giữ" một ảnh
  (điều kiện `status='approved' AND fb_post_id IS NULL`), cộng với `concurrency` của Actions.
- **Lỗi token không retry**: `OAuthException`/`code 190` → dừng ngay, báo Telegram.
- **Lỗi từng ảnh không làm hỏng cả lần chạy**: mỗi ảnh có try/except riêng.
- **Chế độ duyệt tay**: `require_approval=true` → worker chỉ chuyển ảnh sang `captioned`,
  chỉ ảnh `approved` mới được gửi lên Facebook.
- **AI không bịa**: prompt cấm bịa giá/kích cỡ/chất liệu; nếu `confidence < 0.5` hoặc có
  cảnh báo nghiêm trọng → **buộc duyệt tay** kể cả khi đã tắt duyệt tay.
- **Múi giờ**: mọi tính toán theo `Asia/Ho_Chi_Minh`, lưu DB dạng UTC.
- **RLS**: cả 4 bảng + Storage đều bật RLS, chỉ chủ sở hữu truy cập;
  `service_role key` chỉ tồn tại trong GitHub Secrets.

---

## Kiểm thử

```bash
python -m pytest worker/tests -q
```

Bao phủ: chia khung giờ (giới hạn bài/ngày, jitter, lead-time, horizon, múi giờ),
parse/validate JSON caption, quy tắc buộc duyệt tay, phân loại lỗi Facebook.

---

## Câu hỏi thường gặp

**Có vi phạm chính sách Facebook không?**
Không — dùng Graph API chính thức với quyền `pages_manage_posts`, không giả lập trình duyệt,
không scraping, không đăng nhập bằng mật khẩu.

**Có đăng được nhiều Page không?**
Bản này thiết kế cho **1 người, 1 Page** (đúng phạm vi đặc tả). Nhiều Page cần mở rộng schema.

**Ảnh có bị công khai không?**
Không. Bucket `photos` là **private**, ảnh gửi lên Facebook bằng upload nhị phân.

**Sửa caption của bài đã lên lịch?**
App chặn sửa (vì bài đã nằm trên Facebook). Muốn sửa: xoá bài lên lịch đó trên Facebook →
bấm **Hủy lịch trong app** → sửa → worker sẽ xếp lại.

---

## Giấy phép

MIT — dùng cho mục đích cá nhân/kinh doanh nhỏ của bạn.
