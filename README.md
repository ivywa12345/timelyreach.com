# TimelyReach 华人时讯

面向海外华人的新闻 + 论坛网站（风格类似华人网“华人闲话”）：

- **自动新闻**：每 4 小时从华尔街日报、彭博、纽约邮报、ZeroHedge、推特抓取文章，用 AI 编译成通俗、吸引眼球的中文帖子，每篇都是一个可以跟帖的论坛主题。
- **网络热帖**：从文学城、华人网、一亩三分地、新未名空间、小红书抓热门帖子做成导读，读者在本站接着讨论。
- **论坛**：读者用邮箱或 GitHub 注册，可以发帖、跟帖（楼层式），有积分和会员等级（新手上路 → 元老），站长/版主可以置顶、锁帖、删帖、禁言。
- **SEO**：每篇新闻和网友帖都有静态页面，带标题/描述/关键词、Open Graph、JSON-LD（NewsArticle、DiscussionForumPosting、面包屑）、`sitemap.xml`、Google 新闻 `sitemap-news.xml`、`robots.txt`、RSS。
- **手机和电脑都适配**，支持深色模式。
- **全部免费托管**：网站在 Cloudflare Pages，定时任务在 GitHub Actions，用户和帖子数据在 Supabase 免费版。

## 架构

```
GitHub Actions（每 4 小时）
  ├─ RSSHub 容器 ─┐
  ├─ pipeline/ (Python)：抓取 → AI 编译成中文 → data/*.json（提交回仓库）
  └─ 生成静态网站 _site/ → 部署到 Cloudflare Pages (timelyreach.com)

浏览器
  └─ site/static/js/app.js ── Supabase（注册登录、发帖、跟帖、积分、浏览数）
```

| 目录 | 内容 |
| --- | --- |
| `pipeline/config/sources.yaml` | 新闻/论坛来源，增删改都在这里 |
| `pipeline/config/site.yaml` | 站名、SEO 描述、版块、站长 GitHub 账号 |
| `pipeline/timelyreach/` | 抓取、AI 编译、生成网站的 Python 代码 |
| `site/templates/`, `site/static/` | 页面模板、CSS、前端脚本 |
| `supabase/schema.sql` | 论坛数据库（表、积分、等级、权限） |
| `data/` | 机器人生成的文章（自动提交） |

## 一次性设置（约 20 分钟）

### 1. 默认分支

仓库的默认分支现在是 `claude/gifted-galileo-iqcyo2`（第一次推送的分支自动成了默认分支），定时任务在它上面运行，**不用改也能用**。
想改名为 `main`：仓库 **Settings → General → Default branch** 旁边的铅笔图标，把分支重命名为 `main`。

### 2. 部署到 Cloudflare Pages 并绑定域名

1. 注册/登录 <https://dash.cloudflare.com>（免费版就够）。
2. **拿 Account ID**：首页右侧栏 “Account ID”，或者 **Workers & Pages** 页面右侧，点复制。
3. **建 API Token**：右上角头像 → **My Profile → API Tokens → Create Token** → 选 **Custom token**：
   - Permissions：`Account` · `Cloudflare Pages` · `Edit`
   - Account Resources：Include · 你的账号
   - 创建后复制 token（只显示一次）。
4. 到 GitHub 仓库 **Settings → Secrets and variables → Actions → New repository secret**，添加：
   - `CLOUDFLARE_API_TOKEN` = 上面的 token
   - `CLOUDFLARE_ACCOUNT_ID` = 上面的 Account ID
5. 到 **Actions → 抓取新闻并部署网站 → Run workflow** 跑一次。成功后 Cloudflare 里会出现 `timelyreach` 项目，先能通过 `https://timelyreach.pages.dev` 访问。
6. **绑定域名**：
   - 推荐把域名 DNS 托管到 Cloudflare：Cloudflare 首页 **Add a domain** → 输入 `timelyreach.com` → 选 Free 计划 → Cloudflare 给你两个 nameserver，到你买域名的地方（GoDaddy / Namecheap 等）把 nameserver 改成这两个（一般几分钟到几小时生效）。
   - **删除旧 App**：在 Cloudflare 的 **DNS → Records** 里，把旧 App 留下的 `A` / `AAAA` / `CNAME` 记录（`timelyreach.com` 和 `www`）删掉；如果旧 App 在 Vercel / Netlify / Heroku 等平台，也去那边解绑域名或删除项目。
   - **Workers & Pages → timelyreach → Custom domains → Set up a custom domain**，依次添加 `timelyreach.com` 和 `www.timelyreach.com`，Cloudflare 会自动建 DNS 记录和 HTTPS 证书。

### 3. 开通论坛（Supabase 免费版）

1. 在 <https://supabase.com> 用 GitHub 登录，新建项目（区域选 US East 或 US West）。
2. **SQL Editor** → 粘贴 `supabase/schema.sql` 全部内容 → **Run**。
   - 用 GitHub 账号 `ivywa12345` 登录本站的人自动成为**站长**（邮箱注册无法冒充）。
3. **Authentication → URL Configuration**：Site URL 填 `https://timelyreach.com`，Redirect URLs 加 `https://timelyreach.com/**`。
4. **GitHub 登录**：在 GitHub **Settings → Developer settings → OAuth Apps → New OAuth App**，
   Homepage 填 `https://timelyreach.com`，Callback URL 填 `https://<你的项目ID>.supabase.co/auth/v1/callback`；
   把 Client ID / Secret 填到 Supabase **Authentication → Providers → GitHub** 并启用。
5. **Project Settings → API** 复制 Project URL 和 `anon` public key，填到本仓库
   **Settings → Secrets and variables → Actions → Variables**：
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`（这是公开 key，本来就会出现在网页里，安全靠数据库的行级权限）

没配置 Supabase 时网站照样能看新闻，只是不能注册和跟帖。

### 4. 翻译 / 改写引擎

按优先级自动选择，至少配一个效果才好：

| 方式 | 设置 | 效果 | 费用 |
| --- | --- | --- | --- |
| **Claude（推荐）** | Secret `ANTHROPIC_API_KEY` | 编译成自然流畅、口语化、有吸引力的中文新闻 | 默认模型 `claude-opus-5-5`（$4/$20 每百万 token），约 $0.05/篇；想省钱可加 Variable `CLAUDE_MODEL` = `claude-haiku-5-5`（便宜约 40 倍） |
| Gemini 免费额度等 OpenAI 兼容接口 | Variable `LLM_BASE_URL` = `https://generativelanguage.googleapis.com/v1beta/openai`，Variable `LLM_MODEL` = `gemini-2.5-flash`，Secret `LLM_API_KEY` | 也能改写，质量稍弱 | 免费额度内免费 |
| 免费机器翻译（默认兜底） | 不用设置（[deep-translator](https://github.com/nidhaloff/deep-translator)） | 只翻译标题和摘要，不会改写 | 免费 |

Claude 调用开启了服务端 refusal fallback（`fallbacks: "default"`）：万一某篇被模型拒绝，会自动换模型重试；仍失败就退回到下一个引擎。

### 5. 可选：推特和小红书

- 推特：Secret `TWITTER_AUTH_TOKEN` = 你（或小号）X 账号浏览器 cookie 里的 `auth_token`。关注的账号在 `sources.yaml` 的 `twitter` 一节修改。
- 小红书：没有公开热榜，反爬严格。设置 Secret `XIAOHONGSHU_COOKIE`，把 `sources.yaml` 里的博主 ID 换成你想跟的，再把 `enabled` 改成 `true`。

### 6. 运行

**Actions → 抓取新闻并部署网站 → Run workflow**。之后每 4 小时自动运行（时间在 workflow 文件的 `cron` 里改）。

### 7. SEO 收录

1. [Google Search Console](https://search.google.com/search-console) 添加域名 `timelyreach.com`（DNS TXT 验证），提交 `https://timelyreach.com/sitemap.xml` 和 `https://timelyreach.com/sitemap-news.xml`。
2. [Bing Webmaster Tools](https://www.bing.com/webmasters) 可以直接从 Google Search Console 导入。
3. 在 `pipeline/config/site.yaml` 里调整站名、描述和关键词。

## 站长荐读（推荐文章 + 你的点评）

1. 打开仓库 **Issues → New issue → 站长荐读**。
2. 填原文链接、你的点评（几句到几段都行，不要贴全文）、可选的一两句摘录、要发到的版块。标题可以留空。
3. 提交后自动运行：机器人以你的口吻把点评整理成帖子（有 Claude key 时会润色、起标题；没有时原样发布你的点评），发到网站，在 issue 里回复帖子链接并关闭 issue。
4. 荐读帖排在首页“今日头条”最前面，楼主显示“站长”。

只认站长账号（`site.yaml` 里的 `admin_github`）开的 issue，别人开的会被忽略。

## 本地运行

```bash
cd pipeline
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q                       # 测试
ANTHROPIC_API_KEY=... python -m timelyreach update --max-new 5   # 抓取并编译
python -m timelyreach build               # 生成 ../_site
python -m http.server -d ../_site 8000    # 打开 http://localhost:8000
```

## 版权和合规

- 新闻是**编译摘要 + 原文链接**，不是全文翻译；华尔街日报、彭博有付费墙，机器人只用标题和摘要写快讯。
- 外站热帖只转标题和导读，引导读者点原帖。
- 有权利人投诉时，删掉 `data/articles/` 里对应的 JSON 并推送即可下线。

## 站长后台与投稿箱（inbox）

- 打开 `https://你的域名/admin.html`，第一次填入 GitHub Fine-grained Token（只选本仓库，Contents: Read and write；只保存在你的浏览器里），之后直接写标题、正文、点发布。
- 文章被保存成 `inbox/*.json`，推送会触发一轮运行，几分钟内发布。外部 Agent 也可以用同样的 JSON 格式往 `inbox/` 里写文件（字段：title、summary、body[]、source_name、source_url、board、tags[]、created）。
- 处理过的文件移到 `inbox/done/`，格式不对的移到 `inbox/rejected/`。
