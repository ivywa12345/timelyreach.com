# TimelyReach 华人时讯 — 项目笔记（新会话先读这里）

## 是什么
面向海外华人的中文财经/新闻站 + 论坛。流水线抓取来源 → 改写成自然中文（Gemini 免费额度）→ 生成帖子和评论 → 静态站，自动部署到 Cloudflare Pages。账号/帖子/评论/浏览数在 Supabase。

## 长期要求（不要违背）
- 署名真实来源媒体，绝不写“文学城”。
- 不使用论坛类来源（一亩三分地、新未名空间、华人网已移除；页面文案也不再提）。
- 浏览数用眼睛图标。品牌 TimelyReach，域名 `www.timelyreach.com`。
- 不造假用户/假互动；不为过 AdSense 审核隐藏来源；不绕过网站的 403 封锁。

## 关键配置
- `pipeline/config/site.yaml`：name、base_url、admin_github=ivywa12345、per_page=40、`ga_measurement_id: G-KTLDSECHMJ`、`gtm_id: GTM-K256QZK2`（GTM 内不要再加同一 G- 的 GA4 配置标签）、`analytics_token` 留空（Pages 自带 Cloudflare 信标，手动加会重复计数）、`adsense_client` 与 `ad_slots` 为空（待填 ca-pub 与广告位 ID）。
- 版块：自动 picks/brief/finance/news/china/hot；用户 invest/money/house/career/chat。
- `pipeline/config/sources.yaml`：WSJ 系列、Economist、Bloomberg、ZeroHedge、NYPost、CNBC、MarketWatch、Fed、USCIS、Zaobao(china 版块, RSSHub)；twitter 与 xiaohongshu 停用；wenxuecity-finance 目前被 403。
- Supabase 项目 `oltegpsgghnbyezypmns`，publishable key 公开可见；GitHub Environment 名为 `SUPABASE_URL`。
- Cloudflare：Pages + `functions/api/post.js`（后台发文）、`cf-stats.js`（需 `CF_STATS_TOKEN`，未设）、`_middleware.js`。

## 排序/首页规则
- 版块：近 4 天最热前 5（HOT_MIN_SCORE=30，简报版块除外），其余按时间倒序。
- 首页：站长荐读只在 12 小时内置顶（最多 2），其后财经/美国新闻交替按最新；下方“最新发布”。
- 周末（周六/日 ≥9:00 ET）自动生成本周回顾，每 ISO 周一篇。

## 管线要点
- `update.py`：inbox → picks → 各来源计划 → 轮询分配 max_new → 简报 → 周报。
- Gemini：lite 优先，brief/weekly/editor 用 flash；失败模型“休息”而非整轮放弃；失败退回免费翻译。
- `inbox/`：JSON 变文章，处理后移至 `inbox/done/`，坏文件到 `inbox/rejected/`。
- 部署前必须测试通过（44 个 pytest）。

## 待办
- Wenxuecity：等用户决定是否改用原始媒体的公开 feed（华尔街见闻、财联社、第一财经等）。
- Search Console 覆盖率报告（noindex / 规范网址问题）待用户提供。
- 设置 `CF_STATS_TOKEN`；AdSense ID；确认 apex→www 跳转；移除 `functions/api/post.js` 里临时的密码长度诊断；确认登录页不再跳动。
