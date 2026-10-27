/* 站长后台：把文章写成 JSON 提交到仓库的 inbox/ 目录（GitHub Contents API）。 */
(function () {
  var cfg = window.TR_CONFIG || {};
  var KEY = "tr_admin_token";
  var $ = function (id) { return document.getElementById(id); };
  var tokenForm = $("token-form"), postForm = $("post-form");

  function getToken() { try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; } }
  function setToken(v) { try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); } catch (e) {} }
  function show() {
    var has = !!getToken();
    tokenForm.hidden = has;
    postForm.hidden = !has;
  }

  function b64(str) {
    var bytes = new TextEncoder().encode(str), bin = "";
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }

  tokenForm.addEventListener("submit", function (e) {
    e.preventDefault();
    setToken(tokenForm.token.value.trim());
    tokenForm.token.value = "";
    show();
  });
  $("token-reset").addEventListener("click", function (e) { e.preventDefault(); setToken(""); show(); });

  postForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var f = postForm, msg = $("post-msg"), btn = $("post-submit");
    var body = f.body.value.split(/\n\s*\n/).map(function (p) { return p.trim(); }).filter(Boolean);
    var owner = !f.source_name.value.trim();
    var now = new Date().toISOString().replace(/\.\d+Z$/, "Z");
    var doc = {
      title: f.title.value.trim(),
      summary: f.summary.value.trim(),
      body: body,
      source_name: f.source_name.value.trim() || "站长",
      source_url: f.source_url.value.trim(),
      board: f.board.value,
      tags: f.tags.value.split(/[,，]/).map(function (t) { return t.trim(); }).filter(Boolean).slice(0, 4),
      created: now,
      author: owner ? "站长" : ""
    };
    var name = "inbox/" + now.slice(0, 10) + "-admin-" + Math.random().toString(16).slice(2, 10) + ".json";
    btn.disabled = true;
    msg.textContent = "提交中…";
    fetch("https://api.github.com/repos/" + cfg.repo + "/contents/" + name, {
      method: "PUT",
      headers: { "Authorization": "Bearer " + getToken(), "Accept": "application/vnd.github+json" },
      body: JSON.stringify({ message: "inbox: " + doc.title, content: b64(JSON.stringify(doc, null, 1)) })
    }).then(function (r) {
      if (r.status === 401 || r.status === 403 || r.status === 404) throw new Error("Token 无效或没有这个仓库的写权限（" + r.status + "）");
      if (!r.ok) throw new Error("GitHub 返回 " + r.status);
      msg.textContent = "已提交！几分钟后自动发布。";
      f.reset();
    }).catch(function (err) {
      msg.textContent = "失败：" + err.message;
    }).then(function () { btn.disabled = false; });
  });

  show();
})();
