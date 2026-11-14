/* Cấu hình công khai của PWA.
 * Chỉ chứa SUPABASE_URL và ANON KEY — hai giá trị này an toàn để công khai
 * vì mọi quyền truy cập đều bị chặn bởi Row Level Security.
 * TUYỆT ĐỐI không đặt service_role key, token Facebook, hay GEMINI_API_KEY ở đây.
 */
window.APP_CONFIG = {
  SUPABASE_URL: "https://YOUR-PROJECT.supabase.co",
  SUPABASE_ANON_KEY: "YOUR_PUBLIC_ANON_KEY",
};
