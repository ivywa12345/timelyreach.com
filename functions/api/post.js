// 站长后台的发布接口：校验后台密码，再用服务器端保存的 GitHub token 把文章写进 inbox/。
// 需要在 Cloudflare Pages 项目里有 ADMIN_PASSWORD 和 ADMIN_GH_TOKEN 两个 secret（由部署流程从 GitHub secrets 同步）。
const REPO = "ivywa12345/timelyreach.com";

function json(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8" } });
}

function sameSecret(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function b64(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

export async function onRequestPost({ request, env }) {
  if (!env.ADMIN_PASSWORD || !env.ADMIN_GH_TOKEN) {
    return json(503, { error: "后台还没配置好：缺少 ADMIN_PASSWORD 或 ADMIN_GH_TOKEN" });
  }
  // The deploy step strips whitespace from secrets, so compare both sides without it.
  const norm = (v) => String(v || "").replace(/\s+/g, "");
  let given = request.headers.get("x-admin-password") || "";
  try { given = decodeURIComponent(given); } catch { /* sent unencoded */ }
  if (!sameSecret(norm(given), norm(env.ADMIN_PASSWORD))) {
    // temporary diagnostic: lengths only, never the values
    const a = norm(given), b = norm(env.ADMIN_PASSWORD);
    return json(401, { error: `后台密码不对（你输入 ${a.length} 位，服务器上是 ${b.length} 位）` });
  }
  let doc;
  try {
    doc = await request.json();
  } catch {
    return json(400, { error: "内容格式不对" });
  }
  if (!doc || !String(doc.title || "").trim() || !Array.isArray(doc.body) || !doc.body.length) {
    return json(400, { error: "标题和正文不能为空" });
  }
  if (doc.check) return json(200, { ok: true }); // password check only

  const created = new Date().toISOString().replace(/\.\d+Z$/, "Z");
  doc.created = created;
  const name = `inbox/${created.slice(0, 10)}-admin-${crypto.randomUUID().slice(0, 8)}.json`;
  const resp = await fetch(`https://api.github.com/repos/${REPO}/contents/${name}`, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${env.ADMIN_GH_TOKEN.trim()}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "timelyreach-admin",
    },
    body: JSON.stringify({ message: `inbox: ${doc.title}`, content: b64(JSON.stringify(doc, null, 1)) }),
  });
  if (!resp.ok) {
    const detail = (await resp.text()).slice(0, 200);
    return json(502, { error: `GitHub 返回 ${resp.status}（token 失效或没有写权限？）`, detail });
  }
  return json(200, { ok: true, file: name });
}
