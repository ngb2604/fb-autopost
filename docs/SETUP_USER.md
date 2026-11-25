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
2. Chạy **lần lượt** các file trong `supabase/migrations/` theo thứ tự tăng dần:
   `0001_init.sql` → `0002_accounts_categories.sql` → `0003_posts.sql` →
   `0004_worker_helpers.sql` → `0005_photo_reserve.sql` → `0006_pwa_post_actions.sql` →
   `0007_fix_profiles_policy.sql` → `0008_auto_classify.sql`.
   Với mỗi file: **copy toàn bộ** nội dung, dán vào editor, bấm **Run**.
3. ✅ Kết quả cần thấy: `Success. No rows returned` sau mỗi file.
4. Kiểm tra: menu **Storage** → phải thấy bucket **photos** (Private, có ổ khóa).

### 1.3 Tạo tài khoản chủ shop (owner)
1. Menu **Authentication** → **Sign In / Providers** → mục **Email**:
   - Bật **Allow new users to sign up**.
   - Bật **Confirm email** = **tắt** (Auto Confirm) để không phải xác nhận email.
2. Mở PWA → tab **Tạo tài khoản** → nhập email + mật khẩu.
3. Nhập **mã mời** (mặc định `MUABAN2026` — đổi trong bảng `app_config` nếu muốn).
4. Tài khoản **đầu tiên** kích hoạt mã mời sẽ tự thành **owner** (chủ shop).
   Người được mời sau khi nhập đúng mã cũng vào được app; người chưa kích hoạt
   mã mời sẽ **không thấy dữ liệu nào** (chặn bằng RLS).

> Muốn thêm người dùng: gửi họ link app + mã mời. Không cần tạo user trong dashboard.
> Muốn thu hồi: xóa user trong **Authentication → Users** (dữ liệu của họ tự xóa theo).


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

> Gói miễn phí: model mặc định `gemini-3.6-flash`, tự chuyển sang
> `gemini-3.5-flash-lite` / `gemini-3.8-flash` khi model chính quá tải.
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

### 3.4 Đổi sang token dài hạn + lấy Page Access Token (tự động)
Trên máy có repo, chạy 1 lệnh (cần `gh` đã đăng nhập và `FB_APP_ID`/`FB_APP_SECRET` trong `.env`):

```bash
python scripts/fb_token.py --token "EAA..." --app-id <App ID> --app-secret <App Secret>
```

Script sẽ: đổi token 1 giờ → token **60 ngày** → gọi `/me/accounts` lấy Page token
→ in ra hạn dùng của cả hai → nạp `FB_PAGE_ID`, `FB_PAGE_ACCESS_TOKEN`, `FB_USER_TOKEN`,
`FB_APP_SECRET`, `FB_APP_ID` lên GitHub Secrets.

- Nếu script báo `An unexpected error has occurred`: token vừa tạo đã "cũ", bấm
  **Generate Access Token** lại trong Explorer rồi chạy script ngay với token mới.
- Cách thủ công (không cần App Secret): <https://developers.facebook.com/tools/debug/accesstoken/>
  → dán token → **Extend Access Token** → nhập mật khẩu Facebook → copy token mới, rồi tự lấy
  `me/accounts` để điền secret.
- `FB_APP_SECRET` **chỉ** dùng để gia hạn token, không đưa vào PWA.

> ✅ Kết quả đúng (đã kiểm tra thực tế): **Page token hết hạn: không hết hạn** — bot chạy vĩnh viễn,
> không phải gia hạn định kỳ. Chỉ khi bạn đổi mật khẩu Facebook hoặc gỡ quyền của app thì mới cần làm lại.

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
**Settings → Secrets and variables → Actions → New repository secret**, thêm đủ các secret sau:

| Tên | Giá trị |
|---|---|
| `SUPABASE_URL` | `https://xxxx.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | khóa secret của Supabase |
| `GEMINI_API_KEY` | `AIza...` |
| `FB_PAGE_ID` | ID Page |
| `FB_PAGE_ACCESS_TOKEN` | `EAA...` (token Page, không hết hạn) |
| `FB_USER_TOKEN` | token người dùng dài hạn — chỉ dùng để gia hạn/khôi phục |
| `FB_APP_ID` | App ID của app Meta |
| `FB_APP_SECRET` | App Secret — **chỉ** dùng để gia hạn token, không lên PWA |
| `TELEGRAM_BOT_TOKEN` | `123456:AAH...` |
| `TELEGRAM_CHAT_ID` | `123456789` |

Tùy chọn — tab **Variables**:
- `GEMINI_MODEL` = `gemini-3.6-flash`
- `GEMINI_MODEL_FALLBACKS` = `gemini-3.5-flash-lite,gemini-3.8-flash`
- `FB_GRAPH_VERSION` = `v26.0`

> Worker chạy hằng ngày, và có thể **Run workflow** với `mode = dry_run | classify | full`
> (xem mục 7).

### 5.3 Host PWA bằng Cloudflare Pages (miễn phí, đã làm sẵn)
App đang chạy tại: **https://fb-autopost.pages.dev/**

Cấu hình (nếu cần làm lại):
1. Đăng nhập https://dash.cloudflare.com → **Workers & Pages** → **Create** → **Pages** →
   **Import an existing Git repository**.
2. Chọn repo `ngb2604/fb-autopost` → **Begin setup**.
3. **Framework preset**: `None`; **Build command**: để trống;
   **Build output directory**: `web`.
4. **Save and Deploy**. Mỗi lần push lên nhánh `main`, Cloudflare tự build + deploy lại.

> Vì sao không dùng GitHub Pages: tài khoản `ngb2604.github.io` đang gắn tên miền riêng
> `giasuconnect.me` nên mọi đường dẫn `ngb2604.github.io/fb-autopost/` bị chuyển hướng
> sang website đang chạy. Cloudflare Pages cho URL riêng, miễn phí, không giới hạn repo public/private.

### 5.4 Trỏ PWA vào Supabase
File `web/config.js` phải có URL và anon key của bạn (xem `README.md`).
Sau khi sửa, commit và push — Cloudflare Pages sẽ tự deploy lại.
(Khi sửa CSS/JS, nhớ tăng `?v=` trong `web/index.html` để iPhone không dùng bản cache cũ.)

> 🖼️ **Đổi logo**: thay file `web/icons/logo-source.jpg` bằng ảnh logo mới, chạy
> `python scripts/make_logo_icons.py` rồi commit + push. Trên iPhone, xoá app khỏi
> Màn hình chính và **Thêm vào MH chính** lại để iOS cập nhật icon (iOS giữ icon cũ
> cho tới khi cài lại).

### 5.5 Bật cron
- Workflow `worker.yml` chạy **08:00 sáng mỗi ngày** (giờ Việt Nam).
- GitHub có thể **tự tắt cron** nếu repo không có hoạt động trong 60 ngày.
  Cách bật lại: tab **Actions** → chọn **Worker đăng bài** → **Enable workflow**,
  hoặc bấm **Run workflow** một lần.

---

## 6. Cài app lên iPhone

1. Mở **https://fb-autopost.pages.dev/** **bằng Safari** (bắt buộc Safari, không dùng Chrome).
2. Bấm nút **Chia sẻ** → **Thêm vào MH chính** (Add to Home Screen).
3. Mở app từ Màn hình chính → **đăng nhập** (hoặc **Tạo tài khoản** + nhập mã mời).
4. App có 5 tab:
   - **Kho ảnh** — thêm ảnh, xem ảnh nào chờ AI phân loại / đã phân loại.
   - **Bài đăng** — mỗi phân loại được gom **toàn bộ ảnh** vào 1 bài (nếu quá nhiều
     thì tối đa 2 bài); duyệt/sửa/bỏ qua/xóa.
   - **Phân loại** — thêm/sửa loại hàng (Ghế văn phòng, Bàn làm việc, Tủ & kệ…),
     kèm ghi chú và hashtag riêng cho từng loại.
   - **Cài đặt** — số bài/ngày, khung giờ, dùng lại ảnh sau bao nhiêu ngày,
     **Tự động phân loại ảnh bằng AI**, đổi mật khẩu, tạm dừng toàn bộ.
   - **Lịch sử** — log các lần chạy và lỗi.
5. Vào **Cài đặt** → điền thông tin shop, 2–3 bài mẫu, số bài/ngày → **Lưu cài đặt**.

---

## 7. Chạy thử và kiểm tra

1. Tab **Kho ảnh** → **Thêm ảnh** → chọn 2–3 ảnh sản phẩm.
   - Ô **phân loại** để mặc định **“🤖 AI tự phân loại”** nếu bạn muốn AI tự gán loại;
     hoặc chọn tay đúng loại hàng.
2. Vào GitHub → **Actions** → **Worker đăng bài** → **Run workflow**:
   - `mode = dry_run`: xem kế hoạch trên log, **không** đăng thật.
   - `mode = classify`: chỉ nhờ AI phân loại ảnh (không tạo bài).
   - `mode = full`: chạy thật (tạo caption + lên lịch Facebook).
3. Kết quả mong đợi:
   - Telegram nhận tin tổng kết (có dòng “Ảnh AI tự phân loại”).
   - Tab **Kho ảnh**: ảnh đã có nhãn loại (ví dụ *Ghế văn phòng*).
   - Tab **Bài đăng**: mỗi phân loại là 1 bài gom toàn bộ ảnh (tối đa 2 bài/loại),
     caption tiếng Việt có emoji, xuống dòng và lời kêu gọi ở cuối.
   - Facebook Page có bài **đã lên lịch** đúng khung giờ.
4. Kiểm tra trên Facebook: **Page → Công cụ → Bài viết đã lên lịch**.

> 🧩 **Cách gom bài**: mỗi lượt chạy, tất cả ảnh của cùng một phân loại được gom
> vào **một bài** để tối ưu. Chỉ khi một loại có quá nhiều ảnh, app mới chia thành
> **tối đa 2 bài**, mỗi bài một caption khác nhau; ảnh dư để lần chạy sau.
> Caption theo mẫu: `🔥 TÊN HÀNG THANH LÝ 🔥` → `💥` câu mở → các dòng `✅`
> đặc điểm → `💼` địa chỉ → `📍` Zalo/Hotline → `📞` mời nhắn tin → `👉` câu kết IN HOA.

> 🔁 **Ảnh được dùng lại**: sau `reuse_days` ngày (mặc định 14), ảnh đã đăng có thể
> xuất hiện lại trong bài mới. Bài mới gom theo phân loại và caption đổi cách mở
> đầu để không bị coi là spam.


---

## 8. Sự cố thường gặp

| Triệu chứng | Cách xử lý |
|---|---|
| Telegram báo "Token Facebook hết hạn" | Làm lại bước 3.3–3.5, cập nhật secret `FB_PAGE_ACCESS_TOKEN`. |
| Log báo `(#200) Requires pages_manage_posts` | Bạn chưa cấp quyền cho Page, hoặc token lấy từ tài khoản không phải admin Page. Làm lại bước 3.2–3.3 và **chọn đúng Page**. |
| Gemini lỗi 429 | Đã chạm giới hạn miễn phí (caption + phân loại dùng chung quota). Giảm `Số ảnh AI phân loại mỗi lần chạy`, giảm số bài/ngày, chờ sang ngày (reset theo giờ Thái Bình Dương). |
| Ảnh mãi ở nhãn “Chờ AI phân loại” | AI chỉ chạy khi worker chạy. Bấm **Actions → Run workflow → mode = classify**. Nếu AI **chưa khớp loại nào**, ảnh được đánh dấu đã thử — hãy chọn loại tay trong **Kho ảnh → bấm ảnh**. |
| Ảnh bị gán sai loại | Mở **Kho ảnh** → bấm ảnh → chọn lại loại → **Lưu**. Nếu muốn AI thử lại: chọn lại **“🤖 Để AI tự phân loại”**. |
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
- ✅ Đăng ký tài khoản mở nhưng **phải có mã mời**; người chưa kích hoạt mã mời
  không đọc/ghi được dữ liệu nào (RLS `is_approved_member()`).
- ✅ Đổi mã mời: Supabase → **Table editor → app_config** (cột `invite_code`).
- ✅ Đổi mật khẩu: trong app → **Cài đặt → Đổi mật khẩu**.
- ❌ Không dán token vào chat, ảnh chụp màn hình hay file trong repo.
