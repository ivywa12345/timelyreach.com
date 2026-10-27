/* 站长后台：把文章交给本站的 /api/post，由服务器写进仓库的 inbox/。 */
(function () {
  var KEY = "tr_admin_pw";
  var $ = function (id) { return document.getElementById(id); };
  var pwForm = $("token-form"), postForm = $("post-form");

  function getPw() { try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; } }
  function setPw(v) { try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); } catch (e) {} }
  function show() {
    var has = !!getPw();
    pwForm.hidden = has;
    postForm.hidden = !has;
  }

  function send(doc, pw) {
    return fetch("/api/post", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Admin-Password": pw },
      body: JSON.stringify(doc)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (r.status === 404 || r.status === 405) throw new Error("发布接口还没部署上（" + r.status + "），请稍后再试");
        if (!r.ok) throw new Error(d.error || ("服务器返回 " + r.status));
        return d;
      });
    });
  }

  pwForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var pw = pwForm.token.value.trim(), msg = $("token-msg");
    msg.textContent = "验证中…";
    send({ check: true, title: "x", body: ["x"] }, pw).then(function () {
      setPw(pw);
      pwForm.token.value = "";
      msg.textContent = "";
      show();
    }).catch(function (err) { msg.textContent = "失败：" + err.message; });
  });
  $("token-reset").addEventListener("click", function (e) { e.preventDefault(); setPw(""); show(); });

  postForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var f = postForm, msg = $("post-msg"), btn = $("post-submit");
    var body = f.body.value.split(/\n\s*\n/).map(function (p) { return p.trim(); }).filter(Boolean);
    var owner = !f.source_name.value.trim();
    var doc = {
      title: f.title.value.trim(),
      summary: f.summary.value.trim(),
      body: body,
      source_name: f.source_name.value.trim() || "站长",
      source_url: f.source_url.value.trim(),
      board: f.board.value,
      tags: f.tags.value.split(/[,，]/).map(function (t) { return t.trim(); }).filter(Boolean).slice(0, 4),
      author: owner ? "站长" : ""
    };
    btn.disabled = true;
    msg.textContent = "提交中…";
    send(doc, getPw()).then(function () {
      msg.textContent = "已提交！几分钟后自动发布。";
      f.reset();
    }).catch(function (err) {
      if (/密码/.test(err.message)) { setPw(""); show(); }
      msg.textContent = "失败：" + err.message;
    }).then(function () { btn.disabled = false; });
  });

  show();
})();
