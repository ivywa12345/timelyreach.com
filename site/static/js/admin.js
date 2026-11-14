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
      headers: { "Content-Type": "application/json", "X-Admin-Password": encodeURIComponent(pw) },
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

  /* ---------- 所见即所得：工具栏 + 实时预览 + 图片上传 ---------- */
  var ta = $("body-text"), pv = $("preview"), fileIn = $("img-file");
  var esc = function (t) { return String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); };
  function inline(t) {
    var o = esc(t);
    o = o.replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/)[^)\s]+)\)/g, '<a href="$2">$1</a>');
    o = o.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>");
    return o;
  }
  var local = {}; // 上传中的图片先用本地数据预览
  function block(b) {
    b = b.trim(); var lines = b.split("\n"), m;
    if (b.indexOf("### ") === 0) return "<h3>" + inline(b.slice(4)) + "</h3>";
    if (b.indexOf("## ") === 0) return "<h2>" + inline(b.slice(3)) + "</h2>";
    if ((m = /^!\[([^\]]*)\]\(((?:https?:\/\/|\/)[^)\s]+)\)$/.exec(b)))
      return '<figure><img src="' + esc(local[m[2]] || m[2]) + '" alt="">' + (m[1] ? "<figcaption>" + esc(m[1]) + "</figcaption>" : "") + "</figure>";
    if (lines.every(function (l) { return l.indexOf("> ") === 0; })) return "<blockquote>" + lines.map(function (l) { return inline(l.slice(2)); }).join("<br>") + "</blockquote>";
    if (lines.every(function (l) { return /^[-•] /.test(l); })) return "<ul>" + lines.map(function (l) { return "<li>" + inline(l.slice(2)) + "</li>"; }).join("") + "</ul>";
    return "<p>" + lines.map(inline).join("<br>") + "</p>";
  }
  function render() {
    var blocks = ta.value.split(/\n\s*\n/).filter(function (x) { return x.trim(); });
    pv.innerHTML = blocks.length ? blocks.map(block).join("") : '<p class="muted">预览会显示在这里</p>';
  }
  ta.addEventListener("input", render);

  function wrap(before, after, placeholder) {
    var s = ta.selectionStart, e = ta.selectionEnd, sel = ta.value.slice(s, e) || placeholder;
    ta.value = ta.value.slice(0, s) + before + sel + after + ta.value.slice(e);
    ta.focus(); ta.selectionStart = s + before.length; ta.selectionEnd = s + before.length + sel.length; render();
  }
  function prefixLines(prefix, placeholder) {
    var s = ta.selectionStart, e = ta.selectionEnd, sel = ta.value.slice(s, e) || placeholder;
    var out = sel.split("\n").map(function (l) { return prefix + l; }).join("\n");
    var lead = s > 0 && ta.value.slice(s - 2, s) !== "\n\n" ? (ta.value.slice(s - 1, s) === "\n" ? "\n" : "\n\n") : "";
    ta.value = ta.value.slice(0, s) + lead + out + "\n\n" + ta.value.slice(e);
    ta.focus(); render();
  }
  function insertAtCursor(text) {
    var s = ta.selectionStart;
    var lead = s > 0 && ta.value.slice(s - 2, s) !== "\n\n" ? (ta.value.slice(s - 1, s) === "\n" ? "\n" : "\n\n") : "";
    ta.value = ta.value.slice(0, s) + lead + text + "\n\n" + ta.value.slice(ta.selectionEnd);
    ta.focus(); render();
  }

  var acts = {
    h2: function () { prefixLines("## ", "小标题"); },
    h3: function () { prefixLines("### ", "小标题"); },
    bold: function () { wrap("**", "**", "粗体"); },
    italic: function () { wrap("*", "*", "斜体"); },
    quote: function () { prefixLines("> ", "引用内容"); },
    list: function () { prefixLines("- ", "要点"); },
    link: function () { var u = prompt("链接地址（https://…）"); if (u) wrap("[", "](" + u.trim() + ")", "链接文字"); },
    image: function () { fileIn.click(); }
  };
  $("tools").addEventListener("click", function (e) {
    var a = e.target && e.target.getAttribute && e.target.closest("button") && e.target.closest("button").getAttribute("data-act");
    if (a && acts[a]) acts[a]();
  });

  function shrink(file) { // 最长边 1600px，转成 JPEG（PNG 带透明的也会铺白底）
    return new Promise(function (resolve, reject) {
      var img = new Image(), url = URL.createObjectURL(file);
      img.onload = function () {
        var k = Math.min(1, 1600 / Math.max(img.width, img.height));
        var c = document.createElement("canvas"); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        var g = c.getContext("2d"); g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height); g.drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url); resolve(c.toDataURL("image/jpeg", 0.85));
      };
      img.onerror = function () { reject(new Error("读不了这张图片")); };
      img.src = url;
    });
  }
  function uploadImage(file) {
    var msg = $("post-msg");
    if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { msg.textContent = "只支持 JPG / PNG / WebP 图片"; return; }
    msg.textContent = "图片处理中…";
    shrink(file).then(function (data) {
      return fetch("/api/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Admin-Password": encodeURIComponent(getPw()) },
        body: JSON.stringify({ data: data })
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (r.status === 404 || r.status === 405) throw new Error("上传接口还没部署上，稍后再试");
          if (!r.ok) throw new Error(d.error || ("服务器返回 " + r.status));
          local[d.url] = data;
          var cap = prompt("图片说明（可空）") || "";
          insertAtCursor("![" + cap.replace(/[\[\]]/g, "") + "](" + d.url + ")");
          msg.textContent = "图片已上传。";
        });
      });
    }).catch(function (err) { msg.textContent = "失败：" + err.message; });
  }
  fileIn.addEventListener("change", function () { if (fileIn.files[0]) uploadImage(fileIn.files[0]); fileIn.value = ""; });
  ta.addEventListener("paste", function (e) {
    var items = (e.clipboardData && e.clipboardData.files) || [];
    if (items.length && /^image\//.test(items[0].type)) { e.preventDefault(); uploadImage(items[0]); }
  });
  ta.addEventListener("dragover", function (e) { e.preventDefault(); });
  ta.addEventListener("drop", function (e) {
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f && /^image\//.test(f.type)) { e.preventDefault(); uploadImage(f); }
  });
  postForm.addEventListener("reset", function () { setTimeout(render, 0); });
  render();
  show();
})();
