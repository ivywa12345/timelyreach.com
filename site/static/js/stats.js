/* 站长数据统计页：全部来自 Supabase 的公开可读表 + search.json 里的标题，不写入任何数据。 */
function start() {
  var CFG = window.TR_CONFIG || {};
  var $ = function (id) { return document.getElementById(id); };
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function num(n) { return Number(n || 0).toLocaleString("en-US"); }

  if (!CFG.supabaseUrl || !CFG.supabaseAnonKey || !window.supabase) {
    var why = [];
    if (!CFG.supabaseUrl) why.push("页面里没有数据库地址");
    if (!CFG.supabaseAnonKey) why.push("页面里没有数据库 key");
    if (!window.supabase) why.push("Supabase 脚本没有加载");
    $("an-off").textContent = "论坛数据库还没有连接，暂时没有数据。（" + why.join("；") + "）";
    $("an-off").hidden = false;
    return;
  }
  var sb = window.supabase.createClient(CFG.supabaseUrl, CFG.supabaseAnonKey);

  function count(table, build) {
    var q = sb.from(table).select("*", { count: "exact", head: true });
    return (build ? build(q) : q).then(function (r) { return r.count || 0; });
  }

  sb.auth.getSession().then(function (r) {
    var user = r.data.session && r.data.session.user;
    if (!user) { $("an-login").hidden = false; return null; }
    return sb.from("profiles").select("role").eq("id", user.id).maybeSingle().then(function (p) {
      if (!p.data || p.data.role !== "admin") { $("an-login").hidden = false; $("an-login").textContent = "这个页面只有站长账号能看。"; return null; }
      return true;
    });
  }).then(function (ok) {
    if (!ok) return;
    $("an-body").hidden = false;
    var since = new Date(Date.now() - 30 * 864e5).toISOString();
    return Promise.all([
      sb.from("thread_stats").select("thread_key,view_count,reply_count").order("view_count", { ascending: false }).limit(1000),
      count("profiles"),
      count("comments", function (q) { return q.eq("deleted", false); }),
      count("threads", function (q) { return q.eq("deleted", false); }),
      sb.from("profiles").select("created_at").gte("created_at", since).order("created_at"),
      sb.from("profiles").select("username,display_name,post_count,comment_count").order("comment_count", { ascending: false }).limit(50),
      fetch("/search.json").then(function (r) { return r.json(); }).catch(function () { return []; })
    ]);
  }).then(function (res) {
    if (!res) return;
    var stats = res[0].data || [], users = res[1], comments = res[2], threads = res[3];
    var signups = res[4].data || [], active = res[5].data || [], index = res[6];

    var byUrl = {};
    index.forEach(function (a) { byUrl[a.u] = a; });
    var boardNames = {};
    (CFG.boards || []).forEach(function (b) { boardNames[b.id] = b.name; });

    var totalViews = stats.reduce(function (s, r) { return s + (r.view_count || 0); }, 0);
    $("an-cards").innerHTML = [
      ["累计浏览", num(totalViews)], ["注册用户", num(users)], ["评论", num(comments)], ["读者发帖", num(threads)]
    ].map(function (c) { return '<div class="an-card"><b>' + c[1] + "</b><span>" + c[0] + "</span></div>"; }).join("");

    var top = stats.filter(function (r) { return r.thread_key.indexOf("t:") === 0 && (r.view_count || 0) > 0; }).slice(0, 10);
    $("an-top").innerHTML = top.length ? top.map(function (r) {
      var url = "/t/" + r.thread_key.slice(2), a = byUrl[url];
      return '<li><a href="' + url + '">' + esc(a ? a.t : r.thread_key) + '</a><span class="n">' + num(r.view_count) + " 浏览 · " + num(r.reply_count) + " 评论</span></li>";
    }).join("") : "<li>还没有浏览数据。</li>";

    var perBoard = {};
    stats.forEach(function (r) {
      var a = r.thread_key.indexOf("t:") === 0 ? byUrl["/t/" + r.thread_key.slice(2)] : null;
      if (a) perBoard[a.b] = (perBoard[a.b] || 0) + (r.view_count || 0);
    });
    var rows = Object.keys(perBoard).map(function (b) { return [b, perBoard[b]]; }).sort(function (x, y) { return y[1] - x[1]; });
    var max = rows.length ? rows[0][1] : 1;
    $("an-boards").innerHTML = rows.length ? rows.map(function (r) {
      return '<div class="row"><span>' + esc(boardNames[r[0]] || r[0]) + '</span><div><div class="bar" style="width:' + Math.max(1, Math.round(r[1] / max * 100)) + '%"></div></div><span class="v">' + num(r[1]) + "</span></div>";
    }).join("") : "<p>还没有数据。</p>";

    var days = [], perDay = {};
    for (var i = 29; i >= 0; i--) { var d = new Date(Date.now() - i * 864e5).toISOString().slice(0, 10); days.push(d); perDay[d] = 0; }
    signups.forEach(function (s) { var d = String(s.created_at).slice(0, 10); if (d in perDay) perDay[d]++; });
    var peak = Math.max.apply(null, days.map(function (d) { return perDay[d]; })) || 1;
    $("an-signups").innerHTML = days.map(function (d) {
      var v = perDay[d];
      return '<div class="col' + (v ? "" : " zero") + '" style="height:' + (v ? Math.max(6, Math.round(v / peak * 100)) : 2) + '%" title="' + d + "：" + v + ' 人"></div>';
    }).join("");

    var act = active.map(function (u) { return [u, (u.post_count || 0) * 3 + (u.comment_count || 0)]; })
      .filter(function (x) { return x[1] > 0; }).sort(function (a, b) { return b[1] - a[1]; }).slice(0, 10);
    $("an-users").innerHTML = act.length ? act.map(function (x) {
      var u = x[0];
      return '<li><a href="/user.html?u=' + encodeURIComponent(u.username) + '">' + esc(u.display_name || u.username) + '</a><span class="n">' + num(u.post_count) + " 帖 · " + num(u.comment_count) + " 评论</span></li>";
    }).join("") : "<li>还没有人发帖或评论。</li>";

    $("an-note").textContent = "浏览数包含站长自己的访问；“最受欢迎”按累计浏览排序。每日浏览趋势需要新增“每日浏览”表，之后补上。";
  }).catch(function (e) { if (window.console) console.error(e); });
}
// Deferred scripts run in document order, and this one sits above config.js / supabase.js in the page.
// While they run readyState is "interactive", so wait for the window load event instead.
if (document.readyState === "complete") start();
else window.addEventListener("load", start);
