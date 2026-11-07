// Traffic numbers from Cloudflare Web Analytics for the owner-only stats page.
// Needs Pages secrets ADMIN_PASSWORD, CF_STATS_TOKEN (API token with Account Analytics: Read) and CF_ACCOUNT_ID,
// all synced from GitHub secrets on deploy. The Web Analytics site tag can be overridden with CF_SITE_TAG.
const DEFAULT_SITE_TAG = "a5eb0553616d4001b827fe56e2f4cf51"; // the beacon Cloudflare Pages injects

function json(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });
}

function sameSecret(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

const norm = (v) => String(v || "").replace(/\s+/g, "");

function query(group, dims, order) {
  return `${group}: rumPageloadEventsAdaptiveGroups(filter: $filter, limit: ${dims ? 10 : 1}, orderBy: [${order}]) { count sum { visits } ${dims ? `dimensions { ${dims} }` : ""} }`;
}

export async function onRequestGet({ request, env }) {
  if (!env.CF_STATS_TOKEN || !env.CF_ACCOUNT_ID || !env.ADMIN_PASSWORD) {
    return json(503, { error: "还没配置好：缺少 CF_STATS_TOKEN / CF_ACCOUNT_ID / ADMIN_PASSWORD" });
  }
  let given = request.headers.get("x-admin-password") || "";
  try { given = decodeURIComponent(given); } catch { /* sent unencoded */ }
  if (!sameSecret(norm(given), norm(env.ADMIN_PASSWORD))) return json(401, { error: "后台密码不对" });

  const days = Math.min(90, Math.max(1, parseInt(new URL(request.url).searchParams.get("days") || "30", 10) || 30));
  const until = new Date();
  const since = new Date(until.getTime() - days * 864e5);
  const gql = `query ($acc: String!, $filter: AccountRumPageloadEventsAdaptiveGroupsFilter_InputObject!) {
    viewer { accounts(filter: { accountTag: $acc }) {
      ${query("total", "", "count_DESC")}
      ${query("byDay", "date", "date_ASC").replace("limit: 10", "limit: 100")}
      ${query("byCountry", "countryName", "sum_visits_DESC")}
      ${query("byReferrer", "refererHost", "sum_visits_DESC")}
      ${query("byDevice", "deviceType", "sum_visits_DESC")}
      ${query("byPath", "requestPath", "sum_visits_DESC")}
    } } }`;
  const filter = {
    siteTag: (env.CF_SITE_TAG || DEFAULT_SITE_TAG).trim(),
    datetime_geq: since.toISOString(),
    datetime_leq: until.toISOString(),
  };
  const resp = await fetch("https://api.cloudflare.com/client/v4/graphql", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.CF_STATS_TOKEN.trim()}`, "Content-Type": "application/json" },
    body: JSON.stringify({ query: gql, variables: { acc: env.CF_ACCOUNT_ID.trim(), filter } }),
  });
  let data;
  try { data = await resp.json(); } catch { return json(502, { error: `Cloudflare 返回 ${resp.status}，内容无法解析` }); }
  if (!resp.ok || (data.errors && data.errors.length)) {
    return json(502, { error: "Cloudflare 接口报错", detail: (data.errors || []).map((e) => e.message).join("；").slice(0, 400) || String(resp.status) });
  }
  const acc = (((data.data || {}).viewer || {}).accounts || [])[0];
  if (!acc) return json(502, { error: "Cloudflare 没有返回这个账号的数据（账号 ID 或令牌权限不对？）" });

  const rows = (group, key) => (acc[group] || []).map((r) => ({ name: (r.dimensions || {})[key] || "（直接访问）", visits: (r.sum || {}).visits || 0, views: r.count || 0 }));
  const total = (acc.total || [])[0] || {};
  return json(200, {
    days,
    visits: (total.sum || {}).visits || 0,
    views: total.count || 0,
    byDay: rows("byDay", "date"),
    countries: rows("byCountry", "countryName"),
    referrers: rows("byReferrer", "refererHost"),
    devices: rows("byDevice", "deviceType"),
    pages: rows("byPath", "requestPath"),
  });
}
