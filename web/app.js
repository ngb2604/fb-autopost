/* Auto Post — PWA quản lý kho ảnh, phân loại & bài đăng Facebook Page.
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
  categories: [],
  posts: [],
  postPhotos: {},        // post_id -> [photo]
  photoById: new Map(),
  profile: null,
  settings: null,
  filter: "all",
  catFilter: "all",
  postFilter: "all",
  signedCache: new Map(),
  editingPostId: null,
  editingCategoryId: null,
  editingPhotoId: null,
  uploading: false,
  renderSeq: 0,
};

const PHOTO_STATUS = {
  pending: { label: "Chưa dùng", cls: "st-pending" },
  captioned: { label: "Chưa dùng", cls: "st-pending" },
  approved: { label: "Chưa dùng", cls: "st-pending" },
  scheduled: { label: "Chờ đăng", cls: "st-scheduled" },
  posted: { label: "Đã dùng", cls: "st-posted" },
  failed: { label: "Lỗi", cls: "st-failed" },
  skipped: { label: "Đã bỏ", cls: "st-skipped" },
};

const POST_STATUS = {
  draft: { label: "Chờ duyệt", cls: "st-captioned" },
  ready: { label: "Chờ xếp lịch", cls: "st-approved" },
  scheduling: { label: "Đang xử lý", cls: "st-scheduled" },
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

function catName(id) {
  if (!id) return "Chưa phân loại";
  const c = state.categories.find((x) => x.id === id);
  return c ? c.name : "Chưa phân loại";
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
/* Supabase / xác thực                                                 */
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

async function loadProfile() {
  if (!user) return null;
  const { data, error } = await sb
    .from("profiles")
    .select("id,email,display_name,role,approved")
    .eq("id", user.id)
    .maybeSingle();
  if (error) {
    console.warn("Không đọc được profile:", error.message);
    return null;
  }
  state.profile = data;
  return data;
}

function showOnly(view) {
  ["login-view", "invite-view", "app-view"].forEach((id) => {
    $(id).hidden = id !== view;
  });
}

async function renderAuth() {
  if (!session) {
    showOnly("login-view");
    return;
  }
  const profile = await loadProfile();
  if (!profile || !profile.approved) {
    $("invite-email").textContent = user.email || "";
    showOnly("invite-view");
    return;
  }
  showOnly("app-view");
  if (!bootstrapApp._done) bootstrapApp();
}

async function login(email, password) {
  const { error } = await sb.auth.signInWithPassword({ email, password });
  if (error) throw error;
}

async function signup({ email, password, displayName, inviteCode }) {
  const { data, error } = await sb.auth.signUp({
    email,
    password,
    options: { data: { display_name: displayName || "" } },
  });
  if (error) throw error;
  if (!data.session) {
    return { needsConfirm: true };
  }
  const { data: ok, error: rpcErr } = await sb.rpc("redeem_invite", { p_code: inviteCode });
  if (rpcErr) throw rpcErr;
  return { activated: !!ok };
}

async function redeemInvite(code) {
  const { data, error } = await sb.rpc("redeem_invite", { p_code: code });
  if (error) throw error;
  return !!data;
}

/* ------------------------------------------------------------------ */
/* Settings                                                            */
/* ------------------------------------------------------------------ */

async function loadSettings() {
  const { data, error } = await sb.from("settings").select("*").limit(1).maybeSingle();
  if (error) throw error;
  if (data) {
    state.settings = data;
    return data;
  }
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
  $("album_size").value = s.album_size ?? 4;
  $("album-out").textContent = s.album_size ?? 4;
  $("reuse_days").value = s.reuse_days ?? 14;
  $("max_posts_per_run").value = s.max_posts_per_run ?? 6;
  $("category_rotation").checked = s.category_rotation !== false;
  const styles = s.post_styles || ["album", "single"];
  $("style_rotation").checked = styles.includes("single");
  $("tone").value = s.tone ?? "";
  $("shop_info").value = s.shop_info ?? "";
  $("sample_posts").value = (s.sample_posts || []).join("\n\n");
  $("default_hashtags").value = (s.default_hashtags || []).join(", ");
  $("require_approval").checked = !!s.require_approval;
  $("paused").checked = !!s.paused;
  $("retention_days").value = s.retention_days ?? 7;
}

async function saveSettings(event) {
  event.preventDefault();
  const payload = {
    posts_per_day: Math.max(1, Math.min(10, +$("posts_per_day").value || 3)),
    time_windows: parseWindows($("time_windows").value),
    jitter_minutes: Math.max(0, Math.min(60, +$("jitter_minutes").value || 0)),
    album_size: Math.max(1, Math.min(10, +$("album_size").value || 4)),
    reuse_days: Math.max(0, Math.min(365, +$("reuse_days").value || 0)),
    max_posts_per_run: Math.max(1, Math.min(20, +$("max_posts_per_run").value || 6)),
    category_rotation: $("category_rotation").checked,
    post_styles: $("style_rotation").checked ? ["album", "single"] : ["album"],
    tone: $("tone").value.trim(),
    shop_info: $("shop_info").value.trim(),
    sample_posts: parseList($("sample_posts").value, "\n\n").map((x) => x.trim()),
    default_hashtags: parseList($("default_hashtags").value),
    require_approval: $("require_approval").checked,
    paused: $("paused").checked,
    retention_days: Math.max(0, Math.min(365, +$("retention_days").value || 0)),
  };
  const owner = state.settings?.owner_id || user.id;
  const { error } = await sb.from("settings").update(payload).eq("owner_id", owner);
  if (error) return toast("Lỗi lưu: " + error.message);
  state.settings = { ...state.settings, ...payload };
  $("settings-status").hidden = false;
  setTimeout(() => ($("settings-status").hidden = true), 2200);
}

async function changePassword(event) {
  event.preventDefault();
  const p1 = $("new_password").value;
  const p2 = $("new_password2").value;
  if (p1.length < 6) return toast("Mật khẩu cần ít nhất 6 ký tự");
  if (p1 !== p2) return toast("Hai mật khẩu không giống nhau");
  const { error } = await sb.auth.updateUser({ password: p1 });
  if (error) return toast("Lỗi đổi mật khẩu: " + error.message);
  $("new_password").value = "";
  $("new_password2").value = "";
  $("password-status").hidden = false;
  setTimeout(() => ($("password-status").hidden = true), 2600);
  toast("Đã đổi mật khẩu");
}

/* ------------------------------------------------------------------ */
/* Phân loại                                                           */
/* ------------------------------------------------------------------ */

async function loadCategories() {
  const { data, error } = await sb
    .from("categories")
    .select("*")
    .order("sort_order", { ascending: true })
    .order("name", { ascending: true });
  if (error) {
    toast("Không tải được phân loại: " + error.message);
    return;
  }
  state.categories = data || [];
}

function fillCategorySelects() {
  const opts = ['<option value="">— Chưa phân loại —</option>']
    .concat(
      state.categories
        .filter((c) => c.active)
        .map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`)
    )
    .join("");
  const upload = $("upload-category");
  const keep = upload.value;
  upload.innerHTML = opts || '<option value="">— Chưa phân loại —</option>';
  if ([...upload.options].some((o) => o.value === keep)) upload.value = keep;

  const box = $("category-filters");
  box.innerHTML = "";
  const addChip = (key, label) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip" + (state.catFilter === key ? " active" : "");
    btn.textContent = label;
    btn.onclick = () => {
      state.catFilter = key;
      fillCategorySelects();
      renderLibrary();
    };
    box.appendChild(btn);
  };
  addChip("all", "Mọi loại");
  state.categories.forEach((c) => addChip(c.id, c.name));
  addChip("none", "Chưa phân loại");
}

function renderCategories() {
  const box = $("category-list");
  box.innerHTML = "";
  const rows = state.categories;
  $("categories-empty").hidden = rows.length > 0;

  rows.forEach((cat) => {
    const count = state.photos.filter((p) => p.category_id === cat.id).length;
    const item = document.createElement("div");
    item.className = "item single";
    item.innerHTML = `
      <div class="body">
        <div class="row-title">
          <strong>${escapeHtml(cat.name)}</strong>
          <span class="badge ${cat.active ? "st-posted" : "st-skipped"}" style="position:static">${cat.active ? "Đang dùng" : "Tắt"}</span>
        </div>
        <div class="meta">${count} ảnh${cat.hint ? " · " + escapeHtml(cat.hint) : ""}</div>
        <div class="meta">${(cat.hashtags || []).map((h) => "#" + escapeHtml(h)).join(" ")}</div>
        <div class="actions">
          <button class="btn small" data-act="edit">Sửa</button>
          <button class="btn small danger" data-act="del">Xóa</button>
        </div>
      </div>`;
    item.querySelector('[data-act="edit"]').onclick = () => openCategoryDialog(cat.id);
    item.querySelector('[data-act="del"]').onclick = () => deleteCategory(cat);
    box.appendChild(item);
  });
}

function openCategoryDialog(id = null) {
  state.editingCategoryId = id;
  const cat = id ? state.categories.find((c) => c.id === id) : null;
  $("category-title").textContent = cat ? "Sửa phân loại" : "Thêm phân loại";
  $("category-name").value = cat?.name || "";
  $("category-hint").value = cat?.hint || "";
  $("category-hashtags").value = (cat?.hashtags || []).map((h) => "#" + h).join(" ");
  $("category-active").checked = cat ? cat.active !== false : true;
  $("category-error").hidden = true;
  $("category-dialog").showModal();
}

async function saveCategory() {
  const name = $("category-name").value.trim();
  if (!name) {
    $("category-error").textContent = "Nhập tên phân loại";
    $("category-error").hidden = false;
    return;
  }
  const payload = {
    name,
    hint: $("category-hint").value.trim(),
    hashtags: parseList($("category-hashtags").value.replace(/#/g, " #"), " ")
      .map((t) => t.replace(/^#+/, ""))
      .filter(Boolean),
    active: $("category-active").checked,
  };
  let error;
  if (state.editingCategoryId) {
    ({ error } = await sb.from("categories").update(payload).eq("id", state.editingCategoryId));
  } else {
    const sort = Math.max(0, ...state.categories.map((c) => c.sort_order || 0)) + 1;
    ({ error } = await sb.from("categories").insert({ ...payload, sort_order: sort }));
  }
  if (error) {
    $("category-error").textContent = /duplicate|unique/i.test(error.message)
      ? "Tên phân loại này đã có"
      : error.message;
    $("category-error").hidden = false;
    return;
  }
  $("category-dialog").close();
  await loadCategories();
  fillCategorySelects();
  renderCategories();
  renderLibrary();
  toast("Đã lưu phân loại");
}

async function deleteCategory(cat) {
  if (!confirm(`Xóa phân loại "${cat.name}"? Ảnh vẫn còn, chỉ mất nhãn phân loại.`)) return;
  const { error } = await sb.from("categories").delete().eq("id", cat.id);
  if (error) return toast("Lỗi xóa: " + error.message);
  await loadCategories();
  fillCategorySelects();
  renderCategories();
  renderLibrary();
  toast("Đã xóa phân loại");
}

/* ------------------------------------------------------------------ */
/* Ảnh                                                                 */
/* ------------------------------------------------------------------ */

async function signedUrl(path) {
  if (!path) return null;
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
    .limit(400);
  if (error) {
    toast("Không tải được kho ảnh: " + error.message);
    return;
  }
  state.photos = data || [];
  state.photoById = new Map(state.photos.map((p) => [p.id, p]));
}

async function uploadPhotos(files) {
  if (state.uploading) return;
  const list = [...files];
  if (!list.length) return;

  const categoryId = $("upload-category").value || null;

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
        category_id: categoryId,
        status: "pending",
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
  renderLibrary();
  renderCategories();
  await loadPosts();
  renderPosts();
}

async function deletePhoto(photo) {
  const msg = photo.status === "scheduled"
    ? "Ảnh này đang nằm trong một bài chờ đăng. Xóa ảnh khỏi kho?"
    : "Xóa ảnh này khỏi kho? (bài đã đăng trên Facebook không bị ảnh hưởng)";
  if (!confirm(msg)) return;
  try {
    await sb.storage.from("photos").remove([photo.storage_path]);
  } catch (_) { /* file có thể đã bị worker dọn */ }
  const { error } = await sb.from("photos").delete().eq("id", photo.id);
  if (error) return toast("Lỗi xóa: " + error.message);
  state.photos = state.photos.filter((p) => p.id !== photo.id);
  renderLibrary();
  renderCategories();
  await loadPosts();
  renderPosts();
  toast("Đã xóa ảnh");
}

/* ------------------------------------------------------------------ */
/* Render: kho ảnh                                                     */
/* ------------------------------------------------------------------ */

const FILTERS = [
  ["all", "Tất cả"],
  ["pending", "Chưa dùng"],
  ["scheduled", "Chờ đăng"],
  ["posted", "Đã dùng"],
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

function matchesPhotoFilter(p) {
  if (state.filter !== "all") {
    if (state.filter === "pending" && !["pending", "captioned", "approved"].includes(p.status)) return false;
    if (state.filter !== "pending" && p.status !== state.filter) return false;
  }
  if (state.catFilter === "none" && p.category_id) return false;
  if (state.catFilter !== "all" && state.catFilter !== "none" && p.category_id !== state.catFilter) return false;
  return true;
}

async function renderLibrary() {
  const seq = ++state.renderSeq;
  renderFilters();
  const grid = $("photo-grid");
  const rows = state.photos.filter(matchesPhotoFilter);
  $("library-empty").hidden = rows.length > 0;
  grid.innerHTML = "";

  const visible = rows.slice(0, 80);
  const urls = await Promise.all(visible.map((p) => signedUrl(p.storage_path)));
  if (seq !== state.renderSeq) return;

  visible.forEach((photo, i) => {
    const meta = PHOTO_STATUS[photo.status] || { label: photo.status, cls: "" };
    const card = document.createElement("div");
    card.className = "photo-card";
    card.innerHTML = `
      ${urls[i] ? `<img src="${urls[i]}" alt="" loading="lazy" />` : ""}
      <span class="badge ${meta.cls}">${meta.label}</span>
      <span class="cat-tag">${escapeHtml(catName(photo.category_id))}${photo.use_count > 1 ? " · ×" + photo.use_count : ""}</span>
      <button class="del" type="button" aria-label="Xóa">✕</button>`;
    card.querySelector(".del").onclick = (e) => {
      e.stopPropagation();
      deletePhoto(photo);
    };
    card.onclick = () => openPhotoDialog(photo.id);
    grid.appendChild(card);
  });
}

/* ------------------------------------------------------------------ */
/* Modal sửa ảnh (gán phân loại)                                       */
/* ------------------------------------------------------------------ */

function openPhotoDialog(id) {
  const photo = state.photos.find((p) => p.id === id);
  if (!photo) return;
  state.editingPhotoId = id;

  const meta = PHOTO_STATUS[photo.status] || { label: photo.status };
  $("photo-meta").textContent = `${meta.label} · đã dùng ${photo.use_count || 0} lần`;
  $("photo-preview").src = "";
  signedUrl(photo.storage_path).then((url) => {
    if (url) $("photo-preview").src = url;
  });

  const select = $("photo-category");
  select.innerHTML =
    '<option value="">— Chưa phân loại —</option>' +
    state.categories
      .map(
        (c) =>
          `<option value="${c.id}"${c.active ? "" : " disabled"}>${escapeHtml(c.name)}${c.active ? "" : " (đã tắt)"}</option>`
      )
      .join("");
  select.value = photo.category_id || "";
  $("photo-dialog").showModal();
}

async function savePhotoDialog() {
  const photo = state.photos.find((p) => p.id === state.editingPhotoId);
  if (!photo) return;
  const categoryId = $("photo-category").value || null;
  const { error } = await sb.from("photos").update({ category_id: categoryId }).eq("id", photo.id);
  if (error) return toast("Lỗi lưu: " + error.message);
  photo.category_id = categoryId;
  $("photo-dialog").close();
  renderLibrary();
  renderCategories();
  await loadPosts();
  renderPosts();
  toast("Đã lưu phân loại");
}

/* ------------------------------------------------------------------ */
/* Render: bài đăng                                                    */
/* ------------------------------------------------------------------ */

const POST_FILTERS = [
  ["all", "Tất cả"],
  ["draft", "Chờ duyệt"],
  ["ready", "Chờ xếp lịch"],
  ["scheduled", "Đã lên lịch"],
  ["posted", "Đã đăng"],
  ["failed", "Lỗi"],
];

function renderPostFilters() {
  const box = $("post-filters");
  box.innerHTML = "";
  POST_FILTERS.forEach(([key, label]) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip" + (state.postFilter === key ? " active" : "");
    btn.textContent = label;
    btn.onclick = () => {
      state.postFilter = key;
      renderPosts();
    };
    box.appendChild(btn);
  });
}

async function loadPosts() {
  const { data: posts, error } = await sb
    .from("posts")
    .select("*")
    .order("created_at", { ascending: false })
    .limit(150);
  if (error) {
    toast("Không tải được bài đăng: " + error.message);
    return;
  }
  state.posts = posts || [];
  state.postPhotos = {};
  const ids = state.posts.map((p) => p.id);
  if (!ids.length) return;

  const { data: links } = await sb
    .from("post_photos")
    .select("post_id,photo_id,position")
    .in("post_id", ids)
    .order("position", { ascending: true });
  const photoIds = [...new Set((links || []).map((l) => l.photo_id))];
  let photos = [];
  if (photoIds.length) {
    const { data } = await sb
      .from("photos")
      .select("id,storage_path,status,use_count")
      .in("id", photoIds);
    photos = data || [];
  }
  const byId = new Map(photos.map((p) => [p.id, p]));
  (links || []).forEach((l) => {
    const ph = byId.get(l.photo_id);
    if (ph) (state.postPhotos[l.post_id] ||= []).push(ph);
  });
}

function thumbsHtml(photos, max = 4) {
  const shown = photos.slice(0, max);
  const extra = photos.length - shown.length;
  return (
    shown.map((p, i) => `<img data-src="${escapeHtml(p.storage_path)}" alt="" loading="lazy" class="ph-${i}" />`).join("") +
    (extra > 0 ? `<span class="ph-more">+${extra}</span>` : "")
  );
}

async function hydrateThumbs(root) {
  const imgs = [...root.querySelectorAll("img[data-src]")];
  await Promise.all(
    imgs.map(async (img) => {
      const url = await signedUrl(img.dataset.src);
      if (url) img.src = url;
      img.removeAttribute("data-src");
    })
  );
}

function renderPosts() {
  renderPostFilters();
  const box = $("post-list");
  box.innerHTML = "";
  const rows = state.posts.filter(
    (p) => state.postFilter === "all" || p.status === state.postFilter
  );
  $("posts-empty").hidden = rows.length > 0;

  rows.forEach((post) => {
    const meta = POST_STATUS[post.status] || { label: post.status, cls: "" };
    const photos = state.postPhotos[post.id] || [];
    const styleLabel = post.style === "album" ? `Album ${photos.length} ảnh` : "1 ảnh";
    const item = document.createElement("div");
    item.className = "post-item";

    let actions = `<button class="btn small" data-act="edit">Sửa</button>`;
    if (post.status === "draft") {
      actions = `<button class="btn small primary" data-act="approve">Duyệt &amp; xếp lịch</button>` + actions;
    }
    if (post.status === "failed" || post.status === "skipped") {
      actions = `<button class="btn small" data-act="retry">Thử lại</button>` + actions;
    }
    if (post.status === "scheduled") {
      actions = `<button class="btn small" data-act="cancel">Hủy lịch</button>` + actions;
    }
    if (["draft", "ready", "failed", "skipped"].includes(post.status)) {
      actions += `<button class="btn small danger" data-act="skip">Bỏ qua</button>`;
    }
    if (["failed", "skipped"].includes(post.status)) {
      actions += `<button class="btn small danger" data-act="del">Xóa</button>`;
    }
    if (post.status === "posted" && post.fb_post_id) {
      actions += `<a class="btn small" href="https://www.facebook.com/${encodeURIComponent(post.fb_post_id)}" target="_blank" rel="noopener">Xem trên Facebook</a>`;
    }

    item.innerHTML = `
      <div class="head">
        <span class="badge ${meta.cls}">${meta.label}</span>
        <strong>${escapeHtml(catName(post.category_id))}</strong>
        <span>· ${styleLabel}</span>
        ${post.scheduled_at ? `<span>· ${fmtDateTime(post.scheduled_at)}</span>` : ""}
      </div>
      <div class="thumbs">${thumbsHtml(photos)}</div>
      <div class="cap">${escapeHtml(post.caption || "(chưa có caption)")}</div>
      ${post.error_message ? `<div class="meta warn">${escapeHtml(post.error_message)}</div>` : ""}
      <div class="actions">${actions}</div>`;

    item.querySelectorAll("[data-act]").forEach((btn) => {
      btn.addEventListener("click", () => postAction(btn.dataset.act, post));
    });

    box.appendChild(item);
    hydrateThumbs(item);
  });
}

async function setPostStatus(post, status) {
  const { data, error } = await sb.rpc("pwa_set_post_status", {
    p_id: post.id,
    p_status: status,
  });
  if (error) return toast("Lỗi: " + error.message);
  if (data === false) return toast("Không đổi được trạng thái bài này");
  return true;
}

async function postAction(act, post) {
  if (act === "edit") return openPostEditor(post.id);

  if (act === "cancel") {
    if (!confirm("Hủy lịch trong app? Nhớ xóa bài đã lên lịch trên Facebook, nếu không sẽ có 2 bài.")) return;
    if (await setPostStatus(post, "ready")) {
      toast("Đã hủy lịch trong app");
    }
  } else if (act === "retry" || act === "approve") {
    if (await setPostStatus(post, "ready")) toast("Bài sẽ được xếp lịch ở lần chạy tới");
  } else if (act === "skip") {
    if (!confirm("Bỏ qua bài này? Ảnh sẽ quay lại kho để dùng cho bài khác.")) return;
    if (await setPostStatus(post, "skipped")) toast("Đã bỏ qua bài");
  } else if (act === "del") {
    if (!confirm("Xóa bài này khỏi danh sách? (bài trên Facebook không bị ảnh hưởng)")) return;
    const { error } = await sb.from("posts").delete().eq("id", post.id);
    if (error) return toast("Lỗi xóa: " + error.message);
    toast("Đã xóa bài");
  }

  await loadPhotos();
  await loadPosts();
  renderLibrary();
  renderPosts();
  renderHistory();
}

/* ------------------------------------------------------------------ */
/* Modal sửa bài                                                       */
/* ------------------------------------------------------------------ */

async function openPostEditor(id) {
  const post = state.posts.find((p) => p.id === id);
  if (!post) return;
  state.editingPostId = id;

  $("edit-caption").value = post.caption || "";
  $("edit-hashtags").value = (post.hashtags || []).join(" ");
  $("edit-time").value = toLocalInput(post.scheduled_at);

  const isScheduled = post.status === "scheduled";
  $("edit-caption").readOnly = isScheduled;
  $("edit-hashtags").readOnly = isScheduled;
  $("edit-time-wrap").hidden = !isScheduled;

  const thumbs = $("edit-thumbs");
  thumbs.innerHTML = thumbsHtml(state.postPhotos[id] || [], 8);
  hydrateThumbs(thumbs);

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

  $("edit-dialog").showModal();
}

async function savePostEditor() {
  const post = state.posts.find((p) => p.id === state.editingPostId);
  if (!post) return;

  if (post.status === "scheduled") {
    if (!confirm("Hủy lịch trong app? Nhớ xóa bài đã lên lịch trên Facebook, nếu không sẽ có 2 bài.")) return;
    if (!(await setPostStatus(post, "ready"))) return;
    $("edit-dialog").close();
    await loadPosts();
    renderPosts();
    return toast("Đã hủy lịch, bài sẽ được xếp lại");
  }

  const hashtags = parseList($("edit-hashtags").value.replace(/#/g, " #"), " ")
    .map((t) => (t.startsWith("#") ? t : "#" + t));
  const { error } = await sb
    .from("posts")
    .update({ caption: $("edit-caption").value.trim(), hashtags })
    .eq("id", post.id);
  if (error) return toast("Lỗi lưu: " + error.message);
  post.caption = $("edit-caption").value.trim();
  post.hashtags = hashtags;
  $("edit-dialog").close();
  renderPosts();
  toast("Đã lưu");
}

/* ------------------------------------------------------------------ */
/* Render: lịch sử                                                     */
/* ------------------------------------------------------------------ */

async function renderHistory() {
  const done = state.posts
    .filter((p) => p.status === "posted" || p.status === "failed")
    .slice(0, 60);

  const box = $("history-list");
  box.innerHTML = "";
  if (!done.length) {
    box.innerHTML = `<p class="empty">Chưa có bài nào được đăng.</p>`;
  }
  done.forEach((post) => {
    const isPosted = post.status === "posted";
    const photos = state.postPhotos[post.id] || [];
    const item = document.createElement("div");
    item.className = "post-item";
    item.innerHTML = `
      <div class="head">
        <span>${isPosted ? "✅ Đã đăng" : "⚠️ Lỗi"}</span>
        <strong>${escapeHtml(catName(post.category_id))}</strong>
        <span>· ${photos.length} ảnh</span>
        <span>· ${fmtDateTime(isPosted ? post.posted_at : post.created_at)}</span>
      </div>
      <div class="thumbs">${thumbsHtml(photos)}</div>
      <div class="cap">${escapeHtml(post.caption || "(không có caption)")}</div>
      ${!isPosted && post.error_message ? `<div class="meta warn">${escapeHtml(post.error_message)}</div>` : ""}
      ${post.fb_post_id && isPosted
        ? `<div class="actions"><a class="btn small" href="https://www.facebook.com/${encodeURIComponent(post.fb_post_id)}" target="_blank" rel="noopener">Xem trên Facebook</a></div>`
        : ""}`;
    box.appendChild(item);
    hydrateThumbs(item);
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
    row.className = "item single";
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
  ["library", "posts", "categories", "settings", "history"].forEach((tab) => {
    $(`tab-${tab}`).hidden = tab !== name;
  });
  [...$("tabbar").children].forEach((btn) =>
    btn.classList.toggle("active", btn.dataset.tab === name)
  );
  $("view-title").textContent = {
    library: "Kho ảnh",
    posts: "Bài đăng",
    categories: "Phân loại",
    settings: "Cài đặt",
    history: "Lịch sử",
  }[name];
  if (name === "history") renderHistory();
}

async function bootstrapApp() {
  bootstrapApp._done = true;
  try {
    const s = await loadSettings();
    fillSettingsForm(s);
    $("account-email").textContent =
      (user?.email || "") + (state.profile?.role === "owner" ? " · chủ shop" : "");
  } catch (err) {
    toast("Lỗi tải cài đặt: " + err.message);
  }
  await loadCategories();
  fillCategorySelects();
  renderCategories();
  await loadPhotos();
  renderLibrary();
  await loadPosts();
  renderPosts();
}

function bindEvents() {
  // --- đăng nhập / đăng ký
  const authTabs = $("auth-tabs");
  authTabs.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-auth]");
    if (!btn) return;
    [...authTabs.children].forEach((c) => c.classList.remove("active"));
    btn.classList.add("active");
    const mode = btn.dataset.auth;
    $("login-form").hidden = mode !== "login";
    $("signup-form").hidden = mode !== "signup";
  });

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
          : err.message === "Email not confirmed"
            ? "Email chưa được xác nhận. Hãy kiểm tra hộp thư."
            : err.message;
      errEl.hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = "Đăng nhập";
    }
  });

  $("signup-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("signup-btn");
    const errEl = $("signup-error");
    errEl.hidden = true;

    const email = $("signup-email").value.trim();
    const password = $("signup-password").value;
    const password2 = $("signup-password2").value;
    const inviteCode = $("signup-invite").value.trim();
    if (password.length < 6) {
      errEl.textContent = "Mật khẩu cần ít nhất 6 ký tự.";
      errEl.hidden = false;
      return;
    }
    if (password !== password2) {
      errEl.textContent = "Hai mật khẩu không giống nhau.";
      errEl.hidden = false;
      return;
    }

    btn.disabled = true;
    btn.textContent = "Đang tạo…";
    try {
      const res = await signup({
        email,
        password,
        displayName: $("signup-name").value.trim(),
        inviteCode,
      });
      if (res.needsConfirm) {
        errEl.textContent = "Đã tạo tài khoản. Hãy mở email để xác nhận rồi đăng nhập.";
        errEl.hidden = false;
        return;
      }
      if (!res.activated) {
        errEl.textContent = "Mã mời không đúng. Tài khoản đã tạo nhưng chưa kích hoạt.";
        errEl.hidden = false;
        return;
      }
      toast("Tạo tài khoản thành công");
    } catch (err) {
      errEl.textContent =
        /already registered|User already exists/i.test(err.message)
          ? "Email này đã có tài khoản. Hãy đăng nhập."
          : err.message;
      errEl.hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = "Tạo tài khoản";
    }
  });

  $("invite-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("invite-btn");
    const errEl = $("invite-error");
    errEl.hidden = true;
    btn.disabled = true;
    try {
      const ok = await redeemInvite($("invite-code").value.trim());
      if (!ok) {
        errEl.textContent = "Mã mời không đúng. Hỏi chủ shop để lấy mã.";
        errEl.hidden = false;
        return;
      }
      toast("Đã kích hoạt tài khoản");
      renderAuth();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.hidden = false;
    } finally {
      btn.disabled = false;
    }
  });

  const doLogout = async () => {
    await sb.auth.signOut();
    bootstrapApp._done = false;
    state.photos = [];
    state.posts = [];
    state.categories = [];
    state.postPhotos = {};
    state.signedCache.clear();
    state.settings = null;
    state.profile = null;
  };
  $("logout-btn").addEventListener("click", doLogout);
  $("invite-logout").addEventListener("click", doLogout);

  // --- kho ảnh
  $("add-photos-btn").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", async (e) => {
    await uploadPhotos(e.target.files);
    e.target.value = "";
  });

  // --- tabs
  $("tabbar").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-tab]");
    if (btn) switchTab(btn.dataset.tab);
  });

  // --- cài đặt
  $("settings-form").addEventListener("submit", saveSettings);
  $("posts_per_day").addEventListener("input", (e) => {
    $("ppd-out").textContent = e.target.value;
  });
  $("album_size").addEventListener("input", (e) => {
    $("album-out").textContent = e.target.value;
  });
  $("password-form").addEventListener("submit", changePassword);

  // --- phân loại
  $("add-category-btn").addEventListener("click", () => openCategoryDialog(null));
  $("category-cancel").addEventListener("click", () => $("category-dialog").close());
  $("category-save").addEventListener("click", saveCategory);

  // --- sửa bài
  $("edit-cancel").addEventListener("click", () => $("edit-dialog").close());
  $("edit-save").addEventListener("click", savePostEditor);

  // --- sửa ảnh
  $("photo-cancel").addEventListener("click", () => $("photo-dialog").close());
  $("photo-save").addEventListener("click", savePhotoDialog);
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
