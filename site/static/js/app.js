/* TimelyReach forum front end: auth, threads, comments, levels, stats.
 * Talks straight to Supabase with the public anon key; row-level security on
 * the database decides what each visitor may read or write. */
(function () {
  "use strict";

  var CFG = window.TR_CONFIG || {};
  var sb = null;
  if (CFG.supabaseUrl && CFG.supabaseAnonKey && window.supabase) {
    sb = window.supabase.createClient(CFG.supabaseUrl, CFG.supabaseAnonKey);
  }
  var BOARDS = {};
  (CFG.boards || []).forEach(function (b) { BOARDS[b.id] = b; });

  var LEVELS = [
    [0, "新手上路"], [20, "初级会员"], [100, "中级会员"], [300, "高级会员"],
    [800, "资深会员"], [2000, "荣誉会员"], [5000, "元老"]
  ];
  // Reads go through the *_public views, which blank the author of anonymous posts
  // unless the viewer wrote it or moderates.
  var AUTHOR_COLS = "anonymous,author_id,author_username,author_display_name,author_avatar_url,author_points,author_role";

  var me = null;          // auth user
  var myProfile = null;   // profiles row

  // ---------- helpers ----------
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function linkify(text) {
    return esc(text).replace(/https?:\/\/[^\s<>"']+/g, function (url) {
      return '<a href="' + url + '" target="_blank" rel="nofollow ugc noopener">' + url + "</a>";
    });
  }
  function param(name) { return new URLSearchParams(location.search).get(name); }
  function fmtTime(iso) {
    if (!iso) return "";
    var d = new Date(iso), now = Date.now(), diff = (now - d.getTime()) / 1000;
    if (diff < 60) return "刚刚";
    if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
    if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }
  function levelOf(profile) {
    if (!profile) return { name: "游客", cls: "" };
    if (profile.role === "admin") return { name: "站长", cls: "lv-staff" };
    if (profile.role === "moderator") return { name: "版主", cls: "lv-staff" };
    var name = LEVELS[0][1], idx = 0;
    LEVELS.forEach(function (l, i) { if ((profile.points || 0) >= l[0]) { name = l[1]; idx = i; } });
    return { name: name, cls: idx >= 3 ? "lv-high" : "" };
  }
  function displayName(p) { return p ? (p.display_name || p.username || "网友") : "匿名网友"; }
  // Flattened author_* columns from the public views -> profile-like object (null = hidden).
  function authorOf(row) {
    if (!row || !row.author_username) return null;
    return { username: row.author_username, display_name: row.author_display_name, avatar_url: row.author_avatar_url,
             points: row.author_points, role: row.author_role };
  }
  function authorLabel(row) {
    var a = authorOf(row);
    if (!a) return "匿名网友";
    return userLink(a) + (row.anonymous ? ' <span class="muted small">（匿名发布，仅你/版主可见）</span>' : "");
  }
  function avatarHtml(p) {
    if (p && p.avatar_url) return '<img src="' + esc(p.avatar_url) + '" alt="" loading="lazy">';
    return '<span class="ph">' + esc(displayName(p).slice(0, 1)) + "</span>";
  }
  function userLink(p) {
    if (!p) return "匿名网友";
    return '<a href="/user.html?u=' + encodeURIComponent(p.username) + '">' + esc(displayName(p)) + "</a>";
  }
  function isMod() { return myProfile && (myProfile.role === "admin" || myProfile.role === "moderator"); }
  function msg(el, text, ok) {
    if (!el) return;
    el.textContent = text || "";
    el.className = "form-msg " + (text ? (ok ? "ok" : "err") : "");
  }
  function errText(e) {
    var m = (e && (e.message || e.error_description)) || String(e);
    if (/Invalid login credentials/i.test(m)) return "邮箱或密码不对";
    if (/already registered/i.test(m)) return "这个邮箱已经注册过了，请直接登录";
    if (/Email not confirmed/i.test(m)) return "请先去邮箱点确认链接";
    if (/row-level security|permission denied/i.test(m)) return "没有权限（可能已被禁言或帖子已锁定）";
    return m;
  }
  function requireSb(el) {
    if (sb) return true;
    if (el) el.innerHTML = '<p class="notice">论坛互动功能还没有开通（站长需要先配置 Supabase）。</p>';
    return false;
  }

  // ---------- header ----------
  function renderUserbox() {
    var box = $("#userbox");
    if (!box) return;
    if (!me) {
      box.innerHTML = '<a href="/login.html?next=' + encodeURIComponent(location.pathname + location.search) + '">登录 / 注册</a>';
      return;
    }
    var p = myProfile || { username: "", display_name: me.email };
    box.innerHTML =
      '<a href="/new.html">发帖</a>' +
      '<a href="/user.html?u=' + encodeURIComponent(p.username) + '" title="' + esc(displayName(p)) + '">' +
      (p.avatar_url ? '<img class="avatar" src="' + esc(p.avatar_url) + '" alt="">' : esc(displayName(p).slice(0, 6))) + "</a>" +
      '<button type="button" class="btn btn-ghost btn-small" id="logout">退出</button>';
    $("#logout").onclick = function () { sb.auth.signOut().then(function () { location.reload(); }); };
  }

  function loadMe() {
    if (!sb) return Promise.resolve();
    return sb.auth.getSession().then(function (r) {
      me = r.data.session ? r.data.session.user : null;
      if (!me) return;
      return sb.from("profiles").select("*").eq("id", me.id).maybeSingle().then(function (pr) { myProfile = pr.data; });
    });
  }

  // ---------- stats (replies / views) ----------
  function fillStats() {
    if (!sb) return;
    var rows = $all("[data-key]");
    var keys = rows.map(function (r) { return r.getAttribute("data-key"); });
    var art = $("[data-thread-key]");
    if (art && art.getAttribute("data-thread-key")) keys.push(art.getAttribute("data-thread-key"));
    keys = keys.filter(function (k, i) { return k && keys.indexOf(k) === i; });
    if (!keys.length) return;
    sb.from("thread_stats").select("thread_key,view_count,reply_count").in("thread_key", keys).then(function (r) {
      var map = {};
      (r.data || []).forEach(function (s) { map[s.thread_key] = s; });
      rows.forEach(function (row) {
        var s = map[row.getAttribute("data-key")] || { view_count: 0, reply_count: 0 };
        var rep = $(".replies", row), vw = $(".views", row);
        if (rep) rep.textContent = s.reply_count;
        if (vw) vw.textContent = s.view_count;
      });
      if (art) {
        var s = map[art.getAttribute("data-thread-key")] || { view_count: 0, reply_count: 0 };
        $all(".post .views").forEach(function (e) { e.textContent = s.view_count; });
        $all(".post .replies").forEach(function (e) { e.textContent = s.reply_count; });
      }
    });
  }

  function bumpView(key) {
    if (!sb || !key) return;
    var seenKey = "tr-viewed:" + key;
    try { if (sessionStorage.getItem(seenKey)) return; sessionStorage.setItem(seenKey, "1"); } catch (e) { /* private mode */ }
    sb.rpc("bump_view", { key: key }).then(function () {});
  }

  // ---------- reader thread lists ----------
  function userRowHtml(t) {
    return '<li class="row' + (t.pinned ? " pinned" : "") + '" data-key="u:' + t.id + '">' +
      '<div class="row-main"><a class="row-title" href="/thread.html?id=' + t.id + '">' + esc(t.title) + "</a>" +
      '<div class="row-meta"><span class="tag tag-user">网友</span>' + authorLabel(t) + " · <time>" + fmtTime(t.created_at) + "</time></div></div>" +
      '<div class="row-stats"><span class="replies">–</span><span class="views">–</span></div></li>';
  }

  function loadLiveBoards() {
    var lists = $all("[data-live-board]");
    if (!sb || !lists.length) return Promise.resolve();
    return Promise.all(lists.map(function (ul) {
      var board = ul.getAttribute("data-live-board");
      var limit = parseInt(ul.getAttribute("data-limit"), 10) || 20;
      var page = Math.max(1, parseInt(param("page"), 10) || 1);
      var paged = ul.hasAttribute("data-paged");
      var from = paged ? (page - 1) * limit : 0;
      return sb.from("threads_public").select("id,title,created_at,pinned," + AUTHOR_COLS, { count: "exact" })
        .eq("board", board)
        .order("pinned", { ascending: false }).order("created_at", { ascending: false })
        .range(from, from + limit - 1)
        .then(function (r) {
          if (r.error) return;
          var data = r.data || [];
          ul.innerHTML = data.map(userRowHtml).join("");
          var empty = ul.parentNode.querySelector(".live-empty");
          if (empty) empty.hidden = data.length > 0;
          var pager = $("#live-pager");
          if (paged && pager && r.count > limit) {
            var pages = Math.ceil(r.count / limit), html = "";
            if (page > 1) html += '<a href="?page=' + (page - 1) + '">« 上一页</a>';
            html += "<span>第 " + page + " / " + pages + " 页</span>";
            if (page < pages) html += '<a href="?page=' + (page + 1) + '">下一页 »</a>';
            pager.innerHTML = html;
            pager.hidden = false;
          }
        });
    }));
  }

  // ---------- hot list ----------
  var searchIndex = null;
  function loadSearchIndex() {
    if (searchIndex) return Promise.resolve(searchIndex);
    return fetch("/search.json").then(function (r) { return r.json(); }).then(function (d) { searchIndex = d; return d; })
      .catch(function () { searchIndex = []; return []; });
  }

  function loadHotList() {
    var ol = $("#hotlist");
    if (!ol) return;
    if (!sb) { ol.innerHTML = '<li class="muted">开通论坛后显示</li>'; return; }
    var since = new Date(Date.now() - 3 * 86400000).toISOString();
    sb.from("thread_stats").select("thread_key,reply_count,view_count").gt("last_reply_at", since)
      .order("reply_count", { ascending: false }).order("view_count", { ascending: false }).limit(10)
      .then(function (r) {
        var stats = (r.data || []).filter(function (s) { return s.reply_count > 0; });
        if (!stats.length) { ol.innerHTML = '<li class="muted">还没有热帖，快去跟帖吧</li>'; return; }
        var uids = stats.filter(function (s) { return s.thread_key.indexOf("u:") === 0; }).map(function (s) { return s.thread_key.slice(2); });
        var userQ = uids.length ? sb.from("threads_public").select("id,title").in("id", uids) : Promise.resolve({ data: [] });
        Promise.all([loadSearchIndex(), userQ]).then(function (res) {
          var titles = {};
          res[0].forEach(function (a) { titles["t:" + a.u.replace(/^\/t\//, "").replace(/\.html$/, "")] = { t: a.t, u: a.u }; });
          (res[1].data || []).forEach(function (t) { titles["u:" + t.id] = { t: t.title, u: "/thread.html?id=" + t.id }; });
          ol.innerHTML = stats.filter(function (s) { return titles[s.thread_key]; }).map(function (s) {
            var x = titles[s.thread_key];
            return '<li><span><a href="' + esc(x.u) + '">' + esc(x.t) + '</a> <span class="muted small">' + s.reply_count + " 回复</span></span></li>";
          }).join("") || '<li class="muted">还没有热帖</li>';
        });
      });
  }

  function renderLevels() {
    $all("#levels").forEach(function (ul) {
      ul.innerHTML = LEVELS.map(function (l) { return "<li><span>" + l[1] + '</span><span class="muted">' + l[0] + " 积分</span></li>"; }).join("") +
        '<li><span>版主 / 站长</span><span class="muted">管理员任命</span></li>';
    });
  }

  // ---------- comments ----------
  var commentsByID = {};

  function commentHtml(c, floor) {
    var p = authorOf(c), lv = p ? levelOf(p) : { name: "匿名", cls: "" };
    var quote = "";
    if (c.reply_to && commentsByID[c.reply_to]) {
      var q = commentsByID[c.reply_to];
      quote = "<blockquote>回复 " + esc(displayName(authorOf(q))) + "：" + esc(q.body.slice(0, 120)) + (q.body.length > 120 ? "…" : "") + "</blockquote>";
    }
    var tools = '<button type="button" data-reply="' + c.id + '">回复</button>';
    if (me && ((c.author_id && c.author_id === me.id) || isMod())) tools += '<button type="button" data-del="' + c.id + '">删除</button>';
    return '<li class="comment" id="c' + c.id + '">' + avatarHtml(p) +
      '<div><div class="comment-head">' + authorLabel(c) + ' <span class="level ' + lv.cls + '">' + lv.name + "</span></div>" +
      '<div class="comment-meta"><span>' + floor + "楼 · " + fmtTime(c.created_at) + '</span><span class="comment-tools">' + tools + "</span></div>" +
      quote + '<div class="comment-body">' + linkify(c.body) + "</div></div></li>";
  }

  function loadComments(key) {
    var list = $("#comment-list");
    if (!list) return;
    if (!requireSb(list)) return;
    sb.from("comments_public").select("id,body,created_at,reply_to," + AUTHOR_COLS)
      .eq("thread_key", key).order("id", { ascending: true }).limit(1000)
      .then(function (r) {
        if (r.error) { list.innerHTML = '<li class="muted">跟帖加载失败：' + esc(r.error.message) + "</li>"; return; }
        var data = r.data || [];
        commentsByID = {};
        data.forEach(function (c) { commentsByID[c.id] = c; });
        list.innerHTML = data.length ? data.map(function (c, i) { return commentHtml(c, i + 1); }).join("")
          : '<li class="muted" style="padding:10px 0">还没有人跟帖，抢个沙发吧！</li>';
        if (location.hash && /^#c\d+$/.test(location.hash)) { var el = $(location.hash); if (el) el.scrollIntoView(); }
      });
  }

  function renderCommentForm(key) {
    var slot = $("#comment-form-slot");
    if (!slot || !sb) return;
    var locked = $("#comments").getAttribute("data-locked") === "1";
    if (locked) { slot.innerHTML = '<p class="notice">本帖已被锁定，不能再跟帖。</p>'; return; }
    if (!me) {
      slot.innerHTML = '<p class="notice"><a href="/login.html?next=' + encodeURIComponent(location.pathname + location.search) + '">登录或注册</a>后参与讨论（跟帖 +1 积分）。</p>';
      return;
    }
    slot.innerHTML = '<form class="form" id="comment-form"><div id="replying" class="muted small" hidden></div>' +
      '<textarea name="body" required maxlength="5000" rows="4" placeholder="说说你的看法…（请文明发言）"></textarea>' +
      '<div class="form-row"><button class="btn" type="submit">发表跟帖</button>' +
      '<label class="check"><input type="checkbox" name="anonymous"> 匿名跟帖</label></div><p class="form-msg" id="comment-msg" role="status"></p></form>';
    var form = $("#comment-form"), replyTo = null;
    $("#comment-list").addEventListener("click", function (ev) {
      var t = ev.target;
      if (t.hasAttribute("data-reply")) {
        replyTo = parseInt(t.getAttribute("data-reply"), 10);
        var c = commentsByID[replyTo], box = $("#replying");
        box.hidden = false;
        box.innerHTML = "回复 " + esc(displayName(authorOf(c))) + ' <button type="button" class="btn-ghost btn btn-small" id="cancel-reply">取消</button>';
        $("#cancel-reply").onclick = function () { replyTo = null; box.hidden = true; };
        form.body.focus();
      } else if (t.hasAttribute("data-del")) {
        if (!confirm("确定删除这条跟帖？")) return;
        sb.from("comments").update({ deleted: true }).eq("id", t.getAttribute("data-del")).then(function (r) {
          if (r.error) alert(errText(r.error)); else loadComments(key);
        });
      }
    });
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var body = form.body.value.trim(), m = $("#comment-msg");
      if (!body) return;
      form.querySelector("button[type=submit]").disabled = true;
      sb.from("comments").insert({ thread_key: key, body: body, reply_to: replyTo, anonymous: form.anonymous.checked }).then(function (r) {
        form.querySelector("button[type=submit]").disabled = false;
        if (r.error) { msg(m, errText(r.error)); return; }
        form.body.value = ""; replyTo = null; $("#replying").hidden = true;
        msg(m, "跟帖成功，积分 +1", true);
        loadComments(key);
        fillStats();
      });
    });
  }

  function initComments() {
    var sec = $("#comments");
    if (!sec) return;
    var key = sec.getAttribute("data-thread-key");
    if (!key) return;
    loadComments(key);
    renderCommentForm(key);
    bumpView(key);
  }

  // ---------- reader thread page ----------
  function initThreadPage() {
    var view = $("#thread-view");
    if (!view) return Promise.resolve();
    var id = parseInt(param("id"), 10);
    if (!id) { view.innerHTML = "<p>帖子不存在。</p>"; return Promise.resolve(); }
    if (!requireSb(view)) return Promise.resolve();
    return sb.from("threads_public").select("id,board,title,body,pinned,locked,created_at," + AUTHOR_COLS).eq("id", id).maybeSingle().then(function (r) {
      var t = r.data;
      if (!t) { view.innerHTML = "<h1>帖子不存在或已被删除</h1>"; $("#comments").hidden = true; return; }
      document.title = t.title + " - " + document.title.split(" - ").slice(-1)[0];
      var b = BOARDS[t.board];
      if (b) $("#crumb-board").innerHTML = '<a href="/f/' + b.id + '/">' + esc(b.name) + "</a> › ";
      var ta = authorOf(t), lv = ta ? levelOf(ta) : { name: "匿名", cls: "" };
      var tools = "";
      if (me && ((t.author_id && t.author_id === me.id) || isMod())) tools += '<button class="btn btn-ghost btn-small" data-act="delete">删除</button>';
      if (isMod()) {
        tools += '<button class="btn btn-ghost btn-small" data-act="pin">' + (t.pinned ? "取消置顶" : "置顶") + "</button>";
        tools += '<button class="btn btn-ghost btn-small" data-act="lock">' + (t.locked ? "解锁" : "锁帖") + "</button>";
      }
      view.setAttribute("data-thread-key", "u:" + t.id);
      view.innerHTML = '<header class="post-head">' + (b ? '<a class="kicker" href="/f/' + b.id + '/">' + esc(b.name) + "</a>" : "") +
        "<h1>" + esc(t.title) + "</h1>" +
        '<div class="byline"><span>楼主 ' + authorLabel(t) + ' <span class="level ' + lv.cls + '">' + lv.name + "</span></span><span>" + fmtTime(t.created_at) +
        '</span><span>浏览 <span class="views">–</span></span><span>回复 <span class="replies">–</span></span>' + (t.locked ? '<span class="tag">已锁定</span>' : "") + "</div></header>" +
        '<div class="post-body">' + t.body.split(/\n+/).filter(Boolean).map(function (p) { return "<p>" + linkify(p) + "</p>"; }).join("") + "</div>" +
        (tools ? '<div class="post-actions">' + tools + "</div>" : "");
      $all("[data-act]", view).forEach(function (btn) {
        btn.onclick = function () {
          var act = btn.getAttribute("data-act"), patch = {};
          if (act === "delete") { if (!confirm("确定删除这个帖子？")) return; patch.deleted = true; }
          if (act === "pin") patch.pinned = !t.pinned;
          if (act === "lock") patch.locked = !t.locked;
          sb.from("threads").update(patch).eq("id", t.id).then(function (u) {
            if (u.error) alert(errText(u.error));
            else if (act === "delete") location.href = "/f/" + t.board + "/";
            else location.reload();
          });
        };
      });
      var sec = $("#comments");
      sec.setAttribute("data-thread-key", "u:" + t.id);
      sec.setAttribute("data-locked", t.locked ? "1" : "0");
    });
  }

  // ---------- login ----------
  function initLogin() {
    var box = $("#auth-box");
    if (!box) return;
    if (!sb) { $("#auth-off").hidden = false; box.hidden = true; return; }
    var next = param("next") || "/";
    if (!/^\/[^\/]/.test(next) && next !== "/") next = "/";
    if (me) { location.replace(next); return; }
    $("#github-login").onclick = function () {
      sb.auth.signInWithOAuth({ provider: "github", options: { redirectTo: location.origin + next } });
    };
    var mode = "signin", form = $("#email-form"), m = $("#auth-msg");
    $all(".tab").forEach(function (tab) {
      tab.onclick = function () {
        mode = tab.getAttribute("data-tab");
        $all(".tab").forEach(function (t) { t.classList.toggle("on", t === tab); });
        $all(".only-signup").forEach(function (el) { el.hidden = mode !== "signup"; });
        $("#email-submit").textContent = mode === "signup" ? "注册" : "登录";
        form.password.setAttribute("autocomplete", mode === "signup" ? "new-password" : "current-password");
        msg(m, "");
      };
    });
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var email = form.email.value.trim(), pw = form.password.value;
      var p = mode === "signup"
        ? sb.auth.signUp({ email: email, password: pw, options: { data: { nickname: form.nickname.value.trim() }, emailRedirectTo: location.origin + next } })
        : sb.auth.signInWithPassword({ email: email, password: pw });
      p.then(function (r) {
        if (r.error) { msg(m, errText(r.error)); return; }
        if (mode === "signup" && !r.data.session) { msg(m, "注册成功！请去邮箱点击确认链接，然后回来登录。", true); return; }
        location.replace(next);
      });
    });
    $("#forgot").onclick = function (ev) {
      ev.preventDefault();
      var email = form.email.value.trim();
      if (!email) { msg(m, "请先填写邮箱"); return; }
      sb.auth.signInWithOtp({ email: email, options: { shouldCreateUser: false, emailRedirectTo: location.origin + "/user.html" } }).then(function (r) {
        msg(m, r.error ? errText(r.error) : "登录链接已发到你的邮箱，点链接即可登录。", !r.error);
      });
    };
  }

  // ---------- new thread ----------
  function initNew() {
    var form = $("#new-thread");
    if (!form) return;
    if (!sb) { var n = $("#need-login"); n.hidden = false; n.textContent = "论坛发帖功能还没有开通。"; return; }
    if (!me) { $("#need-login").hidden = false; return; }
    form.hidden = false;
    var b = param("board");
    if (b && form.board.querySelector('option[value="' + b + '"]')) form.board.value = b;
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var m = $("#new-msg"), btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      sb.from("threads").insert({ board: form.board.value, title: form.title.value.trim(), body: form.body.value.trim(),
                                  anonymous: !!(form.anonymous && form.anonymous.checked) })
        .select("id").single().then(function (r) {
          btn.disabled = false;
          if (r.error) { msg(m, errText(r.error)); return; }
          location.href = "/thread.html?id=" + r.data.id;
        });
    });
  }

  // ---------- profile ----------
  function initProfile() {
    var view = $("#profile-view");
    if (!view) return;
    if (!requireSb(view)) return;
    var u = param("u");
    if (!u && !me) { view.innerHTML = '<p>请先 <a href="/login.html?next=/user.html">登录</a>。</p>'; return; }
    var q = sb.from("profiles").select("*");
    q = u ? q.eq("username", u) : q.eq("id", me.id);
    q.maybeSingle().then(function (r) {
      var p = r.data;
      if (!p) { view.innerHTML = "<p>用户不存在。</p>"; return; }
      var lv = levelOf(p);
      var next = LEVELS.filter(function (l) { return l[0] > (p.points || 0); })[0];
      view.innerHTML = '<div class="profile-head">' + avatarHtml(p) +
        "<div><h1>" + esc(displayName(p)) + '</h1><span class="muted">@' + esc(p.username) + '</span> <span class="level ' + lv.cls + '">' + lv.name + "</span>" +
        (p.banned ? ' <span class="tag">已禁言</span>' : "") + "</div></div>" +
        (p.bio ? "<p>" + esc(p.bio) + "</p>" : "") +
        "<p>积分 <strong>" + p.points + "</strong>" + (next && p.role === "member" ? '（再得 ' + (next[0] - p.points) + " 分升级为" + next[1] + "）" : "") +
        " · 主题 " + p.post_count + " · 跟帖 " + p.comment_count + " · 注册于 " + fmtTime(p.created_at) + "</p>" +
        (myProfile && myProfile.role === "admin" && p.id !== myProfile.id
          ? '<div class="post-actions"><button class="btn btn-ghost btn-small" id="ban">' + (p.banned ? "解除禁言" : "禁言") + '</button>' +
            '<button class="btn btn-ghost btn-small" id="mod">' + (p.role === "moderator" ? "取消版主" : "设为版主") + "</button></div>" : "");
      document.title = displayName(p) + " - " + document.title.split(" - ").slice(-1)[0];
      if ($("#ban")) $("#ban").onclick = function () { sb.from("profiles").update({ banned: !p.banned }).eq("id", p.id).then(function () { location.reload(); }); };
      if ($("#mod")) $("#mod").onclick = function () { sb.from("profiles").update({ role: p.role === "moderator" ? "member" : "moderator" }).eq("id", p.id).then(function () { location.reload(); }); };
      if (me && p.id === me.id) {
        var edit = $("#profile-edit"), form = $("#profile-form");
        edit.hidden = false;
        form.display_name.value = p.display_name || "";
        form.bio.value = p.bio || "";
        form.onsubmit = function (ev) {
          ev.preventDefault();
          sb.from("profiles").update({ display_name: form.display_name.value.trim(), bio: form.bio.value.trim() }).eq("id", p.id).then(function (x) {
            msg($("#profile-msg"), x.error ? errText(x.error) : "已保存", !x.error);
          });
        };
      }
      sb.from("threads_public").select("id,title,created_at,pinned," + AUTHOR_COLS).eq("author_id", p.id)
        .order("created_at", { ascending: false }).limit(20).then(function (t) {
          var ul = $("#profile-threads");
          ul.innerHTML = (t.data || []).map(userRowHtml).join("") || '<li class="muted">还没有发过主题</li>';
          fillStats();
        });
    });
  }

  // ---------- search ----------
  function initSearch() {
    var out = $("#search-results");
    if (!out) return;
    var q = (param("q") || "").trim();
    $("#q").value = q;
    if (!q) return;
    var terms = q.toLowerCase().split(/\s+/).filter(Boolean);
    out.innerHTML = '<li class="muted">搜索中…</li>';
    var userQ = sb
      ? sb.from("threads_public").select("id,title,created_at,pinned," + AUTHOR_COLS)
          .ilike("title", "%" + q.replace(/[%_]/g, "") + "%").order("created_at", { ascending: false }).limit(30)
      : Promise.resolve({ data: [] });
    Promise.all([loadSearchIndex(), userQ]).then(function (res) {
      var news = res[0].filter(function (a) {
        var hay = (a.t + " " + a.s).toLowerCase();
        return terms.every(function (t) { return hay.indexOf(t) >= 0; });
      }).slice(0, 60);
      var html = news.map(function (a) {
        var b = BOARDS[a.b];
        return '<li class="row"><div class="row-main"><a class="row-title" href="' + esc(a.u) + '">' + esc(a.t) + "</a>" +
          '<div class="row-meta">' + (b ? esc(b.name) + " · " : "") + esc(a.d) + "</div></div></li>";
      }).join("") + (res[1].data || []).map(userRowHtml).join("");
      out.innerHTML = html || '<li class="muted">没有找到相关帖子</li>';
    });
  }

  // ---------- misc ----------
  function initShare() {
    $all("[data-share]").forEach(function (btn) {
      btn.onclick = function () {
        var data = { title: document.title, url: location.href };
        if (navigator.share) { navigator.share(data).catch(function () {}); return; }
        if (navigator.clipboard) navigator.clipboard.writeText(location.href).then(function () { btn.textContent = "链接已复制"; });
      };
    });
  }

  function renderDate() {
    var el = $("#today");
    if (!el) return;
    try {
      el.textContent = new Date().toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "long", timeZone: "America/New_York" }) + "（美东）";
    } catch (e) { /* keep the build date */ }
  }

  function start() {
    renderDate();
    renderLevels();
    initShare();
    loadMe().then(function () {
      renderUserbox();
      initLogin();
      initNew();
      initProfile();
      initSearch();
      return Promise.all([initThreadPage(), loadLiveBoards()]);
    }).then(function () {
      initComments();
      fillStats();
      loadHotList();
    }).catch(function (e) { if (window.console) console.error(e); });
    if (sb) {
      sb.auth.onAuthStateChange(function (event) {
        if (event === "SIGNED_IN" && !me) location.reload();
      });
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
