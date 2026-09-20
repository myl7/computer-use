## 改动方案（3 个文件，均在 /Users/myl/.agents/skills/agent-reach/，~/.claude/skills 为软链无需同步）

### 1. SKILL.md
- 第 75 行注释改为：「网页搜索/阅读：先用 WebSearch / WebFetch；撞上反爬防御（付费墙、403、UA 拦截、人机验证、JS 空壳、地域封锁、限流）再用 Firecrawl——从 firecrawl-* skill 里按场景选一个，见 references/search.md」
- 第 138、142 行「详细文档」一行简介同步为「被反爬防御拦住时回退 Firecrawl skill」
- 全文移除「Firecrawl MCP」措辞

### 2. references/search.md（主要改动）
- 优先级第 3 条：「Firecrawl MCP」→「Firecrawl skill」，触发条件为反爬防御场景
- 「使用场景」表替换为「反爬防御场景 → skill」映射表：
  | 防御表现 | skill |
  |---------|-------|
  | 付费墙/会员墙拿不到正文 | firecrawl-scrape |
  | 403/拒绝访问、Cloudflare/WAF 拦截 | firecrawl-scrape |
  | UA 拦截（非浏览器 UA 被拒） | firecrawl-scrape |
  | 人机验证/挑战中转页 | firecrawl-scrape，不行再 firecrawl-interact |
  | 地域封锁 | firecrawl-scrape（location 参数） |
  | 429 限流重试无效 | firecrawl-search / firecrawl-scrape |
  | 无限滚动/懒加载 | firecrawl-interact |
  | JS 空壳（SPA） | firecrawl-scrape（--wait-for） |
  | 需点击/翻页/简单登录的公开内容 | firecrawl-interact |
  - 补两行文字：搜索本身被拦用 firecrawl-search；正文提取不干净的复杂页用 firecrawl-agent；不确定先看 firecrawl 主 skill 路由表
- 全文不再出现 MCP 工具名

### 3. references/web.md
- 改为窄版「防御场景 → skill」表（scrape / interact 两条主线 + firecrawl-agent 兜底）
- MCP 字样全部移除

### 不动
- frontmatter 的 description 与 NOT for（skill 触发条件不变）
- 批量抓取相关（firecrawl-crawl / firecrawl-download）不进映射表