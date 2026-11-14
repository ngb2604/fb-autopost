/* Auto Post — PWA quản lý kho ảnh & hàng đợi đăng bài Facebook Page.
 * Chỉ dùng anon key + RLS. Không chứa secret nào khác.
 */

const cfg = window.APP_CONFIG || {};
const MAX_EDGE = 1600;
const JPEG_QUALITY = 0.82;
const UPLOAD_CONCURRENCY = 3;
const SIGNED_TTL = 3600;

let sb = null;
let session = null;
let user = null;

const state = {
  photos: [],
  filter: "all",
  settings: null,
  signedCache: new Map(),
  editingId: null,
  uploading: false,
};

const STATUS = {
  pending: { label: "Chờ AI", cls: "st-pending" },
  captioned: { label: "Chờ duyệt", cls: "st-captioned" },
  approved: { label: "Đã duyệt", cls: "st-approved" },
  scheduled: { label: "Đã lên lịch", cls: "st-scheduled" },
  posted: { label: "Đã đăng", cls: "st-posted" },
  failed: { label: "Lỗi", cls: "st-failed" },
  skipped: { label: "Đã bỏ qua", cls: "st-skipped" },
};

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------------ */
/* Tiện ích                                                            */
/* ------------------------------------------------------------------ */

function toast(message, ms = 2600) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.hidden = true), ms);
}

function fmtDateTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString("vi-VN", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function toLocalInput(iso) {
  const d = iso ? new Date(iso) : new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ------------------------------------------------------------------ */
/* Nén ảnh trên trình duyệt                                            */
/* ------------------------------------------------------------------ */

async function compressImage(file) {
  const bitmap = await loadBitmap(file);
  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
  const w = Math.round(bitmap.width * scale);
  const h = Math.round(bitmap.height * scale);

  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d", { alpha: false });
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, w, h);
  ctx.drawImage(bitmap, 0, 0, w, h);
  if (bitmap.close) bitmap.close();

  const blob = await new Promise((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY)
  );
  if (!blob) throw new Error("Không nén được ảnh");
  return blob;
}

/** iOS: createImageBitmap có hỗ trợ xoay theo EXIF; fallback dùng <img>. */
async function loadBitmap(file) {
  if (window.createImageBitmap) {
    try {
      return await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch (_) { /* thử cách dưới */ }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error("Ảnh không đọc được (có thể là HEIC)"));
      el.src = url;
    });
    return img;
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }
}

async function sha256Hex(blob) {
  const buf = await blob.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/* ------------------------------------------------------------------ */
/* Supabase                                                            */
/* ------------------------------------------------------------------ */

async function initSupabase() {
  if (!cfg.SUPABASE_URL || !cfg.SUPABASE_ANON_KEY) {
    throw new Error("Thiếu web/config.js — hãy chạy script setup hoặc điền SUPABASE_URL / ANON_KEY.");
  }
  sb = window.supabase.createClient(cfg.SUPABASE_URL, cfg.SUPABASE_ANON_KEY, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false },
  });
  const { data } = await sb.auth.getSession();
  session = data.session;
  user = session?.user ?? null;

  sb.auth.onAuthStateChange((_event, s) => {
    session = s;
    user = s?.user ?? null;
    renderAuth();
  });
}

function renderAuth() {
  const loggedIn = !!session;
  $("login-view").hidden = loggedIn;
  $("app-view").hidden = !loggedIn;
  if (loggedIn) bootstrapApp();
}

async function login(email, password) {
  const { error } = await sb.auth.signInWithPassword({ email, password });
  if (error) throw error;
}

/* ------------------------------------------------------------------ */
/* Settings                                                            */
/* ------------------------------------------------------------------ */

const DEFAULT_SETTINGS = {
  posts_per_day: 3,
  time_windows: ["09:00", "12:00", "20:00"],
  jitter_minutes: 10,
  require_approval: true,
  tone: "thân thiện, tự nhiên, ngắn gọn",
  shop_info: "",
  sample_posts: [],
  default_hashtags: [],
  paused: false,
  retention_days: 7,
};

async function loadSettings() {
  const { data, error } = await sb.from("settings").select("*").limit(1).maybeSingle();
  if (error) throw error;
  if (data) {
    state.settings = data;
    return data;
  }
  // Lần đầu đăng nhập: tạo settings mặc định cho chính mình
  const { data: created, error: insErr } = await sb
    .from("settings")
    .insert({ owner_id: user.id })
    .select()
    .single();
  if (insErr) throw insErr;
  state.settings = created;
  return created;
}

function fillSettingsForm(s) {
  $("posts_per_day").value = s.posts_per_day ?? 3;
  $("ppd-out").textContent = s.posts_per_day ?? 3;
  $("time_windows").value = (s.time_windows || []).join(", ");
  $("jitter_minutes").value = s.jitter_minutes ?? 10;
  $("tone").value = s.tone ?? "";
  $("shop_info").value = s.shop_info ?? "";
  $("sample_posts").value = (s.sample_posts || []).join("\n\n");
  $("default_hashtags").value = (s.default_hashtags || []).join(", ");
  $("require_approval").checked = !!s.require_approval;
  $("paused").checked = !!s.paused;
  $("retention_days").value = s.retention_days ?? 7;
}

function parseList(value, sep = ",") {
  return String(value || "")
    .split(sep)
    .map((x) => x.trim())
    .filter(Boolean);
}

function parseWindows(value) {
  return parseList(value)
    .map((v) => {
      const m = v.match(/^(\d{1,2})[:h.](\d{2})$/);
      if (!m) return null;
      return `${String(Math.min(23, +m[1])).padStart(2, "0")}:${m[2]}`;
    })
    .filter(Boolean);
}

async function saveSettings(event) {
  event.preventDefault();
  const payload = {
    posts_per_day: Math.max(1, Math.min(10, +$("posts_per_day").value || 3)),
    time_windows: parseWindows($("time_windows").value),
    jitter_minutes: Math.max(0, Math.min(60, +$("jitter_minutes").value || 0)),
    tone: $("tone").value.trim(),
    shop_info: $("shop_info").value.trim(),
    sample_posts: parseList($("sample_posts").value, "\n\n").map((x) => x.trim()),
    default_hashtags: parseList($("default_hashtags").value),
    require_approval: $("require_approval").checked,
    paused: $("paused").checked,
    retention_days: Math.max(0, Math.min(365, +$("retention_days").value || 0)),
  };
  const { error } = await sb.from("settings").update(payload).eq("owner_id", user.id);
  if (error) return toast("Lỗi lưu: " + error.message);
  state.settings = { ...state.settings, ...payload };
  $("settings-status").hidden = false;
  setTimeout(() => ($("settings-status").hidden = true), 2200);
}

/* ------------------------------------------------------------------ */
/* Ảnh                                                                 */
/* ------------------------------------------------------------------ */

async function signedUrl(path) {
  const hit = state.signedCache.get(path);
  if (hit && hit.exp > Date.now()) return hit.url;
  const { data, error } = await sb.storage.from("photos").createSignedUrl(path, SIGNED_TTL);
  if (error) return null;
  state.signedCache.set(path, { url: data.signedUrl, exp: Date.now() + (SIGNED_TTL - 120) * 1000 });
  return data.signedUrl;
}

async function loadPhotos() {
  const { data, error } = await sb
    .from("photos")
    .select("*")
    .order("created_at", { ascending: false })
    .limit(300);
  if (error) {
    toast("Không tải được kho ảnh: " + error.message);
    return;
  }
  state.photos = data || [];
  await renderLibrary();
  renderQueue();
  renderHistory();
}

async function uploadPhotos(files) {
  if (state.uploading) return;
  const list = [...files];
  if (!list.length) return;

  state.uploading = true;
  $("upload-progress").hidden = false;
  const bar = $("upload-progress").querySelector(".bar");
  bar.style.width = "0%";

  let done = 0;
  let added = 0;
  let skipped = 0;
  let failed = 0;

  const worker = async (file) => {
    try {
      const blob = await compressImage(file);
      const hash = await sha256Hex(blob);

      const { data: dup } = await sb
        .from("photos")
        .select("id")
        .eq("content_hash", hash)
        .neq("status", "skipped")
        .limit(1);
      if (dup && dup.length) {
        skipped += 1;
        return;
      }

      const path = `${user.id}/${crypto.randomUUID()}.jpg`;
      const { error: upErr } = await sb.storage.from("photos").upload(path, blob, {
        contentType: "image/jpeg",
        upsert: false,
      });
      if (upErr) throw upErr;

      const { error: dbErr } = await sb.from("photos").insert({
        owner_id: user.id,
        storage_path: path,
        content_hash: hash,
      });
      if (dbErr) {
        await sb.storage.from("photos").remove([path]);
        throw dbErr;
      }
      added += 1;
    } catch (err) {
      console.error(err);
      failed += 1;
    } finally {
      done += 1;
      bar.style.width = `${Math.round((done / list.length) * 100)}%`;
      $("upload-status").textContent = `${done}/${list.length}`;
    }
  };

  // Hàng đợi có giới hạn 3 luồng
  const queue = [...list];
  const runners = Array.from({ length: Math.min(UPLOAD_CONCURRENCY, queue.length) }, async () => {
    while (queue.length) {
      const file = queue.shift();
      await worker(file);
    }
  });
  await Promise.all(runners);

  state.uploading = false;
  $("upload-progress").hidden = true;
  $("upload-status").textContent = "";
  toast(
    `Đã thêm ${added} ảnh` +
      (skipped ? `, bỏ qua ${skipped} ảnh trùng` : "") +
      (failed ? `, ${failed} ảnh lỗi` : "")
  );
  await loadPhotos();
}

async function deletePhoto(photo) {
  if (!confirm("Xóa ảnh này khỏi kho? (bài đã đăng trên Facebook không bị ảnh hưởng)")) return;
  try {
    await sb.storage.from("photos").remove([photo.storage_path]);
  } catch (_) { /* file có thể đã bị worker dọn */ }
  const { error } = await sb.from("photos").delete().eq("id", photo.id);
  if (error) return toast("Lỗi xóa: " + error.message);
  state.photos = state.photos.filter((p) => p.id !== photo.id);
  await renderLibrary();
  renderQueue();
  renderHistory();
  toast("Đã xóa ảnh");
}

/* ------------------------------------------------------------------ */
/* Render: kho ảnh                                                     */
/* ------------------------------------------------------------------ */

const FILTERS = [
  ["all", "Tất cả"],
  ["pending", "Chờ AI"],
  ["captioned", "Chờ duyệt"],
  ["approved", "Đã duyệt"],
  ["scheduled", "Đã lên lịch"],
  ["posted", "Đã đăng"],
  ["failed", "Lỗi"],
];

function renderFilters() {
  const box = $("status-filters");
  if (box.childElementCount) return;
  FILTERS.forEach(([key, label]) => {
    const btn = document.createElement("button");
    btn.className = "chip" + (key === state.filter ? " active" : "");
    btn.textContent = label;
    btn.type = "button";
    btn.onclick = () => {
      state.filter = key;
      [...box.children].forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      renderLibrary();
    };
    box.appendChild(btn);
  });
}

async function renderLibrary() {
  renderFilters();
  const grid = $("photo-grid");
  const rows = state.photos.filter(
    (p) => state.filter === "all" || p.status === state.filter
  );
  $("library-empty").hidden = rows.length > 0;
  grid.innerHTML = "";

  const visible = rows.slice(0, 60);
  const urls = await Promise.all(visible.map((p) => signedUrl(p.storage_path)));

  visible.forEach((photo, i) => {
    const meta = STATUS[photo.status] || { label: photo.status, cls: "" };
    const card = document.createElement("div");
    card.className = "photo-card";
    card.innerHTML = `
      ${urls[i] ? `<img src="${urls[i]}" alt="" loading="lazy" />` : ""}
      <span class="badge ${meta.cls}">${meta.label}</span>
      <button class="del" type="button" aria-label="Xóa">✕</button>`;
    card.querySelector(".del").onclick = (e) => {
      e.stopPropagation();
      deletePhoto(photo);
    };
    if (photo.status === "captioned" || photo.status === "approved") {
      card.onclick = () => openEditor(photo.id);
    }
    grid.appendChild(card);
  });
}

/* ------------------------------------------------------------------ */
/* Render: hàng đợi                                                    */
/* ------------------------------------------------------------------ */

function queueRows() {
  const rank = { captioned: 0, approved: 1, scheduled: 2 };
  return state.photos
    .filter((p) => rank[p.status] !== undefined)
    .sort((a, b) => {
      if (a.status !== b.status) return rank[a.status] - rank[b.status];
      const at = a.scheduled_at ? new Date(a.scheduled_at).getTime() : Infinity;
      const bt = b.scheduled_at ? new Date(b.scheduled_at).getTime() : Infinity;
      return at - bt;
    });
}

function renderQueue() {
  const box = $("queue-list");
  const rows = queueRows();
  $("queue-empty").hidden = rows.length > 0;
  $("queue-count").textContent = rows.length ? `${rows.length} bài` : "";
  box.innerHTML = "";

  rows.forEach((photo) => {
    const meta = STATUS[photo.status];
    const item = document.createElement("div");
    item.className = "item";
    item.innerHTML = `
      <div class="thumb-placeholder" data-thumb="${photo.id}"></div>
      <div class="body">
        <div class="caption">${escapeHtml(photo.caption || "(chưa có caption)")}</div>
        <div class="meta">
          <span class="badge ${meta.cls}" style="position:static;display:inline-block">${meta.label}</span>
          ${photo.scheduled_at ? " · " + fmtDateTime(photo.scheduled_at) : ""}
        </div>
        <div class="actions">
          ${photo.status === "captioned" ? `<button class="btn small primary" data-act="approve">Duyệt</button>` : ""}
          <button class="btn small" data-act="edit">Sửa</button>
          <button class="btn small danger" data-act="skip">Bỏ qua</button>
        </div>
      </div>`;

    item.querySelector('[data-act="approve"]')?.addEventListener("click", () => setStatus(photo.id, "approved"));
    item.querySelector('[data-act="edit"]').addEventListener("click", () => openEditor(photo.id));
    item.querySelector('[data-act="skip"]').addEventListener("click", () => setStatus(photo.id, "skipped"));

    box.appendChild(item);
    signedUrl(photo.storage_path).then((url) => {
      const holder = item.querySelector(`[data-thumb="${photo.id}"]`);
      if (url && holder) {
        const img = document.createElement("img");
        img.src = url;
        img.alt = "";
        img.loading = "lazy";
        holder.replaceWith(img);
      }
    });
  });
}

async function setStatus(id, status) {
  const { error } = await sb.from("photos").update({ status }).eq("id", id);
  if (error) return toast("Lỗi: " + error.message);
  const photo = state.photos.find((p) => p.id === id);
  if (photo) photo.status = status;
  renderQueue();
  renderLibrary();
  toast("Đã cập nhật");
}

async function approveAll() {
  const ids = state.photos.filter((p) => p.status === "captioned").map((p) => p.id);
  if (!ids.length) return toast("Không có bài nào chờ duyệt");
  const { error } = await sb.from("photos").update({ status: "approved" }).in("id", ids);
  if (error) return toast("Lỗi: " + error.message);
  ids.forEach((id) => {
    const p = state.photos.find((x) => x.id === id);
    if (p) p.status = "approved";
  });
  renderQueue();
  renderLibrary();
  toast(`Đã duyệt ${ids.length} bài`);
}

/* ------------------------------------------------------------------ */
/* Modal sửa bài                                                       */
/* ------------------------------------------------------------------ */

async function openEditor(id) {
  const photo = state.photos.find((p) => p.id === id);
  if (!photo) return;
  state.editingId = id;

  $("edit-caption").value = photo.caption || "";
  $("edit-hashtags").value = (photo.hashtags || []).join(" ");
  $("edit-time").value = toLocalInput(photo.scheduled_at);

  const isScheduled = photo.status === "scheduled";
  $("edit-caption").readOnly = isScheduled;
  $("edit-hashtags").readOnly = isScheduled;
  $("edit-time-wrap").hidden = !isScheduled;

  const warn = $("edit-warning");
  if (isScheduled) {
    warn.hidden = false;
    warn.textContent =
      "Bài này đã được lên lịch trên Facebook. Nếu muốn sửa nội dung, hãy xóa bài lên lịch đó trên Facebook rồi bấm “Hủy lịch trong app”.";
    $("edit-save").textContent = "Hủy lịch trong app";
  } else {
    warn.hidden = true;
    $("edit-save").textContent = "Lưu";
  }

  const url = await signedUrl(photo.storage_path);
  $("edit-thumb").src = url || "";
  $("edit-dialog").showModal();
}

async function saveEditor() {
  const photo = state.photos.find((p) => p.id === state.editingId);
  if (!photo) return;

  if (photo.status === "scheduled") {
    if (!confirm("Hủy lịch trong app? Nhớ xóa bài đã lên lịch trên Facebook, nếu không sẽ có 2 bài.")) return;
    const { error } = await sb
      .from("photos")
      .update({
        status: "approved",
        scheduled_at: null,
        fb_post_id: null,
        error_message: "Đã hủy lịch trong app — kiểm tra bài lên lịch cũ trên Facebook.",
      })
      .eq("id", photo.id);
    if (error) return toast("Lỗi: " + error.message);
    Object.assign(photo, { status: "approved", scheduled_at: null, fb_post_id: null });
    $("edit-dialog").close();
    renderQueue();
    renderLibrary();
    return toast("Đã hủy lịch, bài sẽ được xếp lại");
  }

  const hashtags = parseList($("edit-hashtags").value.replace(/#/g, " #"), " ")
    .map((t) => (t.startsWith("#") ? t : "#" + t));
  const { error } = await sb
    .from("photos")
    .update({ caption: $("edit-caption").value.trim(), hashtags })
    .eq("id", photo.id);
  if (error) return toast("Lỗi lưu: " + error.message);
  photo.caption = $("edit-caption").value.trim();
  photo.hashtags = hashtags;
  $("edit-dialog").close();
  renderQueue();
  toast("Đã lưu");
}

/* ------------------------------------------------------------------ */
/* Render: lịch sử                                                     */
/* ------------------------------------------------------------------ */

async function renderHistory() {
  const posted = state.photos
    .filter((p) => p.status === "posted" || p.status === "failed")
    .slice(0, 60);

  const box = $("history-list");
  box.innerHTML = "";
  if (!posted.length) {
    box.innerHTML = `<p class="empty">Chưa có bài nào được đăng.</p>`;
  }
  posted.forEach((photo) => {
    const isPosted = photo.status === "posted";
    const item = document.createElement("div");
    item.className = "item";
    item.innerHTML = `
      <div class="thumb-placeholder" data-thumb="${photo.id}"></div>
      <div class="body">
        <div class="caption">${escapeHtml(photo.caption || "(không có caption)")}</div>
        <div class="meta">
          ${isPosted ? "✅ " + fmtDateTime(photo.posted_at) : "⚠️ " + escapeHtml(photo.error_message || "Lỗi")}
        </div>
        ${photo.fb_post_id && isPosted
          ? `<div class="actions"><a class="btn small" href="https://www.facebook.com/${encodeURIComponent(photo.fb_post_id)}" target="_blank" rel="noopener">Xem trên Facebook</a></div>`
          : ""}
      </div>`;
    box.appendChild(item);
    signedUrl(photo.storage_path).then((url) => {
      const holder = item.querySelector(`[data-thumb="${photo.id}"]`);
      if (url && holder) {
        const img = document.createElement("img");
        img.src = url;
        img.alt = "";
        img.loading = "lazy";
        holder.replaceWith(img);
      }
    });
  });

  const { data: logs } = await sb
    .from("post_logs")
    .select("id,level,message,created_at")
    .order("created_at", { ascending: false })
    .limit(40);

  const logBox = $("log-list");
  logBox.innerHTML = "";
  if (!logs || !logs.length) {
    logBox.innerHTML = `<p class="empty">Chưa có nhật ký.</p>`;
    return;
  }
  logs.forEach((log) => {
    const row = document.createElement("div");
    row.className = "item";
    row.style.gridTemplateColumns = "1fr";
    row.innerHTML = `
      <div class="body">
        <div class="caption">${escapeHtml(log.message)}</div>
        <div class="meta">${log.level === "error" ? "🔴" : log.level === "warn" ? "🟡" : "🔵"} ${fmtDateTime(log.created_at)}</div>
      </div>`;
    logBox.appendChild(row);
  });
}

/* ------------------------------------------------------------------ */
/* Tabs & khởi động                                                    */
/* ------------------------------------------------------------------ */

function switchTab(name) {
  ["library", "queue", "settings", "history"].forEach((tab) => {
    $(`tab-${tab}`).hidden = tab !== name;
  });
  [...$("tabbar").children].forEach((btn) =>
    btn.classList.toggle("active", btn.dataset.tab === name)
  );
  $("view-title").textContent = {
    library: "Kho ảnh",
    queue: "Hàng đợi",
    settings: "Cài đặt",
    history: "Lịch sử",
  }[name];
}

async function bootstrapApp() {
  if (bootstrapApp._done) return loadPhotos();
  bootstrapApp._done = true;
  try {
    const s = await loadSettings();
    fillSettingsForm(s);
  } catch (err) {
    toast("Lỗi tải cài đặt: " + err.message);
  }
  await loadPhotos();
}

function bindEvents() {
  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("login-btn");
    const errEl = $("login-error");
    errEl.hidden = true;
    btn.disabled = true;
    btn.textContent = "Đang đăng nhập…";
    try {
      await login($("login-email").value.trim(), $("login-password").value);
    } catch (err) {
      errEl.textContent =
        err.message === "Invalid login credentials"
          ? "Sai email hoặc mật khẩu."
          : err.message;
      errEl.hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = "Đăng nhập";
    }
  });

  $("logout-btn").addEventListener("click", async () => {
    await sb.auth.signOut();
    bootstrapApp._done = false;
    state.photos = [];
    state.signedCache.clear();
  });

  $("add-photos-btn").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", async (e) => {
    await uploadPhotos(e.target.files);
    e.target.value = "";
  });

  $("tabbar").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-tab]");
    if (btn) switchTab(btn.dataset.tab);
  });

  $("settings-form").addEventListener("submit", saveSettings);
  $("posts_per_day").addEventListener("input", (e) => {
    $("ppd-out").textContent = e.target.value;
  });

  $("approve-all-btn").addEventListener("click", approveAll);

  $("edit-cancel").addEventListener("click", () => $("edit-dialog").close());
  $("edit-save").addEventListener("click", saveEditor);
}

async function main() {
  bindEvents();
  try {
    await initSupabase();
  } catch (err) {
    document.body.innerHTML = `<div style="padding:24px;font-family:system-ui"><h2>Chưa cấu hình</h2><p>${escapeHtml(err.message)}</p></div>`;
    return;
  }
  renderAuth();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
}

main();
