# Hướng dẫn cài đặt (dành cho chủ shop)

Làm đúng thứ tự dưới đây. Mỗi bước có **kết quả cần ghi lại** — hãy mở một ghi chú
(Notes trên iPhone) để dán các giá trị này vào.

> ⏱️ Tổng thời gian: khoảng 30–40 phút. Không cần thẻ tín dụng, không cần VPS.

---

## 0. Bạn cần có

- [ ] Tài khoản Facebook là **quản trị viên** của Page cần đăng bài.
- [ ] Email (dùng cho GitHub, Supabase, Google).
- [ ] iPhone có Safari (để cài PWA).

---

## 1. Supabase (cơ sở dữ liệu + đăng nhập + kho ảnh)

### 1.1 Tạo project
1. Vào <https://supabase.com> → **Start your project** → đăng nhập bằng GitHub/Google.
2. **New project**:
   - Name: `fb-autopost`
   - Database Password: bấm **Generate a password** rồi **lưu lại** (cần khi cần truy cập DB).
   - Region: **Southeast Asia (Singapore)** — gần Việt Nam nhất.
   - Plan: **Free**.
3. Chờ 1–2 phút để project khởi tạo.

### 1.2 Chạy migration (tạo bảng, RLS, bucket ảnh)
1. Trong project → menu trái → **SQL Editor** → **New query**.
2. Mở file `supabase/migrations/0001_init.sql` trong repo, **copy toàn bộ** nội dung, dán vào editor.
3. Bấm **Run** (Ctrl+Enter).
4. ✅ Kết quả cần thấy: `Success. No rows returned`.
5. Kiểm tra: menu **Storage** → phải thấy bucket **photos** (Private, có ổ khóa).

### 1.3 Tạo tài khoản đăng nhập của bạn
1. Menu **Authentication** → **Users** → **Add user** → **Create new user**.
2. Email: email của bạn. Password: mật khẩu mạnh (lưu lại!).
3. Bật **Auto Confirm User** (rất quan trọng, nếu không sẽ phải xác nhận email).
4. Bấm **Create user**.

### 1.4 Tắt đăng ký công khai (bắt buộc vì lý do bảo mật)
1. **Authentication** → **Sign In / Providers** (hoặc **Providers**).
2. Mục **Email** → tắt **Allow new users to sign up** (Disable signups).
3. **Save**.

### 1.5 Lấy 3 giá trị cần dùng
1. **Project Settings** (bánh răng) → **API**.
2. Ghi lại:
   - `SUPABASE_URL` — dạng `https://xxxxxxxx.supabase.co`
   - `SUPABASE_ANON_KEY` — khóa **public** (`anon` / `publishable`) → dùng cho PWA
   - `SUPABASE_SERVICE_ROLE_KEY` — khóa **secret** (`service_role`) → **chỉ dán vào GitHub Secrets**, không bao giờ đưa vào `web/`

> 🔒 Nếu lộ `service_role` key, kẻ khác đọc được toàn bộ dữ liệu. Vào **API → JWT Settings → Generate new JWT secret** để thu hồi và cập nhật lại.

---

## 2. Google AI Studio (Gemini viết caption)

1. Vào <https://aistudio.google.com/apikey> → đăng nhập Google.
2. **Create API key** → chọn project (hoặc để nó tự tạo).
3. Copy khóa → ghi là `GEMINI_API_KEY` (dạng `AIza...`).

> Gói miễn phí: model mặc định là `gemini-3.8-flash` (hỗ trợ ảnh).
> Lưu ý: gói miễn phí có thể dùng dữ liệu bạn gửi để cải thiện sản phẩm Google
> → **không đưa ảnh có mặt khách hàng / thông tin cá nhân** vào.

---

## 3. Meta for Developers (đăng bài lên Page)

> Đây là bước khó nhất. Làm chậm, đúng từng bước.

### 3.1 Tạo Facebook App
1. Vào <https://developers.facebook.com/apps> → **Create app**.
2. Use case: chọn **Other** → Next → App type: **Business** → Next.
3. App name: `Auto Post Page` → chọn Business portfolio (nếu được hỏi) → **Create app**.

### 3.2 Thêm quyền cần thiết
1. Trong app → **App settings → Basic**: ghi lại **App ID** và **App secret** (để dự phòng).
2. Đảm bảo app đang ở **Development mode** (công tắc trên cùng, không cần bật Live).
3. Menu **Use cases** → tìm **Manage everything on your Page** (hoặc **Facebook Page management**) → **Customize**.
4. Trong phần permissions, bấm **Add** cho 3 quyền:
   - `pages_manage_posts`
   - `pages_read_engagement`
   - `pages_show_list`

### 3.3 Lấy Page Access Token bằng Graph API Explorer
1. Mở <https://developers.facebook.com/tools/explorer/>.
2. Góc phải:
   - **Meta App**: chọn app vừa tạo.
   - **User or Page**: chọn **Get User Access Token**.
   - **Permissions**: thêm `pages_show_list`, `pages_manage_posts`, `pages_read_engagement`.
3. Bấm **Generate Access Token** → cửa sổ Facebook hiện ra → **chọn đúng Page** → **Continue** → đồng ý các quyền.
4. Copy token ngắn hạn (dạng `EAA...`).

### 3.4 Đổi sang token dài hạn (60 ngày)
1. Mở <https://developers.facebook.com/tools/debug/accesstoken/>.
2. Dán token vừa copy → **Debug** → kéo xuống **Extend Access Token** → làm theo hướng dẫn.
3. Copy token mới.

### 3.5 Lấy Page ID và Page Access Token
Cách nhanh nhất (dùng Graph API Explorer):
1. Quay lại <https://developers.facebook.com/tools/explorer/>.
2. Ô query, nhập: `me/accounts` → **Submit**.
3. Trong kết quả JSON, tìm Page của bạn:
   - `id` → chính là `FB_PAGE_ID`
   - `access_token` → chính là `FB_PAGE_ACCESS_TOKEN` (**token này không hết hạn nếu lấy từ token dài hạn**)

> ✅ Ghi lại:
> - `FB_PAGE_ID` = ..............................
> - `FB_PAGE_ACCESS_TOKEN` = ..............................
>
> ⏰ Token dài hạn khoảng **60 ngày**. Đặt lịch nhắc trên điện thoại để làm lại bước 3.3–3.5
> trước khi hết hạn. Khi token hết hạn, bot sẽ gửi Telegram cảnh báo và **dừng đăng** (không đăng sai).

---

## 4. Telegram (nhận thông báo)

Mất khoảng 3 phút. Làm trên **điện thoại** (Telegram app) hoặc Telegram Desktop đều được.

### 4.1 Tạo bot với BotFather
1. Mở app Telegram → bấm ô **Tìm kiếm** (kính lúp) → gõ `BotFather`.
2. Chọn tài khoản **@BotFather** (có **dấu tick xanh**, tên hiển thị "BotFather").
3. Bấm nút **START** ở dưới cùng.
4. Bấm ô nhập tin nhắn, gõ chính xác: `/newbot` rồi gửi.
5. BotFather hỏi **"Alright, a new bot. How are we going to call it?"** → gõ tên hiển thị bất kỳ, ví dụ:
   `Ngoc Son Auto Post` → gửi.
6. BotFather hỏi tiếp **username** → gõ một username **chưa ai dùng** và **phải kết thúc bằng chữ `bot`**, ví dụ:
   `ngocson_post_bot` → gửi.
   - Nếu bị báo "Sorry, this username is already taken" → đổi tên khác, ví dụ `ngocson_post2026_bot`.
7. BotFather trả lời **"Done! Congratulations on your new bot..."** và có dòng:
   ```
   Use this token to access the HTTP API:
   8123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
   → **Bấm giữ vào token để Copy** (token dài, có dấu `:` ở giữa). Đây là `TELEGRAM_BOT_TOKEN`.

### 4.2 Bật bot và gửi tin thử
8. Ngay trong tin nhắn của BotFather có link **`t.me/ngocson_post_bot`** → bấm vào → mở khung chat với bot của bạn → bấm **START** → gõ `hi` → gửi.
   (Bắt buộc: nếu chưa bấm START, bot **không** gửi được tin cho bạn.)

### 4.3 Lấy `TELEGRAM_CHAT_ID`
9. Mở trình duyệt trên máy tính, dán địa chỉ sau (thay `<TOKEN>` bằng token ở bước 7):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
10. Trong trang JSON hiện ra, tìm đoạn `"chat":{"id":` → con số ngay sau đó (ví dụ `"id":7351234567`) chính là
    `TELEGRAM_CHAT_ID`. **Chỉ copy phần số** (không copy dấu ngoặc kép).

> Nếu trang trả về `{"ok":true,"result":[]}` (rỗng): quay lại Telegram nhắn cho bot thêm 1 tin nữa (ví dụ `test`),
> đợi 5 giây rồi tải lại trang. Nếu vẫn rỗng, gõ `/start` cho bot rồi thử lại.

> Nếu không muốn làm bước 9–10: chỉ cần gửi token cho trợ lý, phần chat_id sẽ được lấy tự động.
> Chat ID của bạn là thông tin **không bí mật** (chỉ dùng để gửi tin cho chính bạn).

### 4.4 Nạp vào GitHub
11. Vào repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:
    - Name: `TELEGRAM_BOT_TOKEN` — Secret: token ở bước 7.
    - Name: `TELEGRAM_CHAT_ID` — Secret: con số ở bước 10.

---

## 5. GitHub (chứa code + chạy worker theo lịch)

### 5.1 Tạo repo
1. Vào <https://github.com/new>.
2. Repository name: `fb-autopost`.
3. Chọn **Private** (khuyến nghị) → **Create repository**.
4. Đưa code lên (xem `README.md` mục "Đưa code lên GitHub" nếu chưa có).

### 5.2 Dán secret
**Settings → Secrets and variables → Actions → New repository secret**, thêm đúng 7 cái:

| Tên | Giá trị |
|---|---|
| `SUPABASE_URL` | `https://xxxx.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | khóa secret của Supabase |
| `GEMINI_API_KEY` | `AIza...` |
| `FB_PAGE_ID` | ID Page |
| `FB_PAGE_ACCESS_TOKEN` | `EAA...` |
| `TELEGRAM_BOT_TOKEN` | `123456:AAH...` |
| `TELEGRAM_CHAT_ID` | `123456789` |

Tùy chọn — tab **Variables**:
- `GEMINI_MODEL` = `gemini-3.8-flash`
- `FB_GRAPH_VERSION` = `v26.0`
- `MAX_PHOTOS_PER_RUN` = `5`

### 5.3 Bật GitHub Pages (cho PWA)
1. **Settings → Pages** → Source: **GitHub Actions**.
2. Chạy workflow **Deploy PWA lên GitHub Pages** (tab **Actions** → chọn workflow → **Run workflow**).
3. Sau khi chạy xong, link app có dạng:
   `https://<username>.github.io/fb-autopost/`

> Nếu repo để **Private**, GitHub Pages yêu cầu gói trả phí. Khi đó hãy để repo **Public**
> — code không chứa secret nào nên vẫn an toàn (mọi bí mật nằm trong GitHub Secrets).

### 5.4 Trỏ PWA vào Supabase
File `web/config.js` phải có URL và anon key của bạn (xem `README.md`).
Sau khi sửa, commit và push — Pages sẽ tự deploy lại.

### 5.5 Bật cron
- Workflow `worker.yml` chạy **08:00 sáng mỗi ngày** (giờ Việt Nam).
- GitHub có thể **tự tắt cron** nếu repo không có hoạt động trong 60 ngày.
  Cách bật lại: tab **Actions** → chọn **Worker đăng bài** → **Enable workflow**,
  hoặc bấm **Run workflow** một lần.

---

## 6. Cài app lên iPhone

1. Mở link GitHub Pages **bằng Safari** (bắt buộc Safari, không dùng Chrome).
2. Bấm nút **Chia sẻ** → **Thêm vào MH chính** (Add to Home Screen).
3. Mở app từ Màn hình chính → đăng nhập bằng email/mật khẩu đã tạo ở bước 1.3.
4. Vào tab **Cài đặt** → điền:
   - Số bài/ngày (mặc định 3)
   - Khung giờ đăng (mặc định 09:00, 12:00, 20:00)
   - Thông tin shop
   - 2–3 bài mẫu bạn thích
   - Giữ **Bắt buộc duyệt tay** = BẬT trong tuần đầu
5. Bấm **Lưu cài đặt**.

---

## 7. Chạy thử và kiểm tra

1. Tab **Kho ảnh** → **Thêm ảnh** → chọn 2–3 ảnh sản phẩm.
2. Vào GitHub → **Actions** → **Worker đăng bài** → **Run workflow**
   - Lần đầu nên tick `dry_run` để xem kế hoạch trên log mà không đăng thật.
3. Bỏ tick `dry_run`, chạy lại.
4. Kết quả mong đợi:
   - Telegram nhận tin tổng kết.
   - Tab **Hàng đợi** có caption tiếng Việt → bấm **Duyệt**.
   - Chạy worker lần nữa → Facebook Page có bài **đã lên lịch**.
5. Kiểm tra trên Facebook: **Page → Công cụ → Bài viết đã lên lịch**.

---

## 8. Sự cố thường gặp

| Triệu chứng | Cách xử lý |
|---|---|
| Telegram báo "Token Facebook hết hạn" | Làm lại bước 3.3–3.5, cập nhật secret `FB_PAGE_ACCESS_TOKEN`. |
| Log báo `(#200) Requires pages_manage_posts` | Bạn chưa cấp quyền cho Page, hoặc token lấy từ tài khoản không phải admin Page. Làm lại bước 3.2–3.3 và **chọn đúng Page**. |
| Gemini lỗi 429 | Đã chạm giới hạn miễn phí. Giảm `MAX_PHOTOS_PER_RUN`, chờ sang ngày (reset theo giờ Thái Bình Dương). |
| Supabase bị "Paused" | Vào dashboard → **Restore project**. Worker chạy hằng ngày nên thường không bị; nếu bị, bấm Restore rồi chạy worker lại. |
| Không thấy workflow chạy đúng giờ | GitHub cron trễ vài phút là bình thường. Nếu >1 giờ, bật lại workflow (mục 5.5). |
| App iOS không mở được | Phải mở bằng **Safari**, và link phải là `https://`. Nếu Pages chưa bật, màn hình sẽ 404. |
| Ảnh HEIC không upload được | iOS thường tự chuyển sang JPEG. Nếu vẫn lỗi, vào Cài đặt iOS → Camera → Định dạng → **Tương thích nhất**. |
| Muốn dừng khẩn cấp | Tab **Cài đặt** → bật **Tạm dừng toàn bộ** → Lưu. |

---

## 9. Bảo mật — nhắc lại

- ✅ `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`, `FB_PAGE_ACCESS_TOKEN`, `TELEGRAM_BOT_TOKEN`
  chỉ nằm trong **GitHub Secrets**.
- ✅ PWA chỉ chứa `SUPABASE_URL` + `SUPABASE_ANON_KEY` (công khai được, vì có RLS).
- ✅ Đã tắt đăng ký công khai trên Supabase.
- ❌ Không dán token vào chat, ảnh chụp màn hình hay file trong repo.
