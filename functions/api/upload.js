// 站长后台上传图片：校验后台密码，把图片写进仓库的 site/static/uploads/，返回站内地址。
const REPO = "ivywa12345/timelyreach.com";
const MAX_BYTES = 3 * 1024 * 1024;

function json(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8" } });
}
function sameSecret(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function onRequestPost({ request, env }) {
  if (!env.ADMIN_PASSWORD || !env.ADMIN_GH_TOKEN) return json(503, { error: "后台还没配置好" });
  const norm = (v) => String(v || "").replace(/\s+/g, "");
  let given = request.headers.get("x-admin-password") || "";
  try { given = decodeURIComponent(given); } catch { /* sent unencoded */ }
  if (!sameSecret(norm(given), norm(env.ADMIN_PASSWORD))) return json(401, { error: "后台密码不对" });

  let doc;
  try { doc = await request.json(); } catch { return json(400, { error: "内容格式不对" }); }
  const m = /^data:image\/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)$/.exec(String(doc.data || ""));
  if (!m) return json(400, { error: "只支持 JPG / PNG / WebP 图片" });
  if (m[2].length * 0.75 > MAX_BYTES) return json(413, { error: "图片太大（上限 3MB）" });

  const ext = m[1] === "jpeg" ? "jpg" : m[1];
  const day = new Date().toISOString().slice(0, 10);
  const path = `site/static/uploads/${day}-${crypto.randomUUID().slice(0, 8)}.${ext}`;
  const resp = await fetch(`https://api.github.com/repos/${REPO}/contents/${path}`, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${env.ADMIN_GH_TOKEN.trim()}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "timelyreach-admin",
    },
    body: JSON.stringify({ message: `upload: ${path.split("/").pop()}`, content: m[2], branch: env.UPLOAD_BRANCH || undefined }),
  });
  if (!resp.ok) return json(502, { error: `GitHub 返回 ${resp.status}` });
  return json(200, { ok: true, url: "/" + path.replace("site/", "") });
}
