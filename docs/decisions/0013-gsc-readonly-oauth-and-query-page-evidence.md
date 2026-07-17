# ADR-0013：GSC 只读 OAuth 与查询—页面联合证据

- 状态：Accepted
- 日期：2026-07-17
- 复查：2026-10-17

## 背景

GSC Excel 的查询表和网页表是两个独立聚合，不能用关键词重合推断某查询属于某页面。
旧流程却允许页面汇总变化直接进入旧文章修改，导致模型在不知道真实读者任务时重写。
同时，运营者确认 2026-06-22 以前的 GSC 数据错误，不得重新进入分析。

CMS Blog JSON 和 Product JSON 是运营者当前可控的完整资产输入，仍适合手工导入；本决策
不改变五步主流程，也不把 CMS 改成远程自动同步。

## 决策

1. GSC 使用 Google installed-app OAuth 2.0，本机 loopback 回调、PKCE，唯一 scope 为
   `https://www.googleapis.com/auth/webmasters.readonly`；不支持 API Key。
2. 客户端 JSON 与 token 只存本地受限文件。SQLite 只保存站点属性、权限级别、可信起始日
   和同步审计；不得保存 client secret、access token、refresh token 或授权码。
3. 自动匹配只能选择 `sc-domain:` 属性或 URL-prefix 的站点根路径；子目录、单页
   URL-prefix 属性必须拒绝。同步前再次校验已保存属性，不能依赖首次连接状态绕过。
4. 所有 Search Analytics 请求使用 `dataState=final`、`type=web`，不得请求
   2026-06-22 以前的日期。
5. 每次同步保存：
   - date 汇总，用于确认实际最终日期；
   - 最近最多 28 天的 page/query 汇总；
   - 仅在可信边界内存在完整上一 28 天时保存 previous 汇总；
   - 当前及可用上一窗口的 `date + query + page` 联合行。
6. 原始 API 响应保存为不可变导入快照，但不包含请求 Header 或 token。相同快照幂等复用；
   新批次完全保存成功后才切换为活动分析批次。
7. 页面级 GSC 机会默认是诊断：
   - striking-distance/CTR 需要该页当前联合行才可执行；
   - click-loss 同时需要当前与上一窗口联合行；
   - 内容生产再次检查联合证据，防止错误状态绕过机会门槛。
8. Blog/Product JSON 继续手工导入；GSC Excel 保留为没有 OAuth 时的后备，但 Excel
   不会伪造联合数据，因此相关旧文章仍保持 `needs_evidence`。

## 凭据边界

- `.secrets/`、`client_secret_*.apps.googleusercontent.com.json` 和
  `*:Zone.Identifier` 由 Git 忽略。
- `.secrets` 与 `.secrets/google` 权限为 0700；客户端与 token 文件为 0600。
- `.env` 只保存文件路径、可信起始日和 loopback 回调，不保存 OAuth token。
- Uvicorn access log 禁用，避免回调授权码进入访问日志；UI 错误只使用安全归一化消息。
- 删除某次 GSC 导入时，原始批次和联合指标按既有规则物理删除，但同步审计保留并将
  `import_id` 置空。

## 后果

### 正面

- 查询—页面归属成为第一方事实，不再依靠 AI 或词项猜测。
- 错误历史日期有代码级硬边界，不能因重新连接而复活。
- 页面异常与写作动作分离；低量或匿名查询受限页面允许没有可执行建议。
- OAuth 凭据不污染数据库、Git、HTML、快照或日志。
- 多个 URL-prefix 属性并存时不会把单页数据误当作全站数据。

### 代价与限制

- 首次使用必须在 Google 页面完成一次人工授权，token 撤销后需重新连接。
- GSC Search Analytics 仍可能只返回顶部数据行，匿名查询不会返回；没有联合行不代表
  页面没有搜索流量。
- 在 2026-06-22 之后积累满两个 28 天窗口前，click-loss 无法满足完整对比门槛。
- OAuth 状态保存在进程内，授权过程中重启服务会要求重新点击连接。

## 真实复验

- Google Sites API 实际返回根站点 `https://laserpointerhub.com/` 与单页 `/p-B017.html/` 两个可写属性。
- 旧选择器曾误选单页，真实同步只有 23 条汇总、0 条联合行；该导入已按治理规则物理删除，同步审计保留。
- 新选择器与同步前防御校验上线后，根站点真实同步得到 542 条汇总、1,188 条 query + page 联合行，日期为 2026-06-22 至 2026-07-14。
- Windows 侧实际导入页显示正确根属性与联合明细；OAuth 凭据仍未进入 SQLite、Git、HTML 或原始快照。

## 被否决的方案

- API Key：Search Console 私有站点数据不应以 API Key 接入，也不能满足用户授权边界。
- 把 token 写入 SQLite：会把凭据混入业务备份和数据库审计范围。
- 用查询表与页面表做关键词联结：这只是推断，违反项目的数据事实边界。
- 继续让页面汇总直接生成重写稿：无法证明具体查询意图，正是本轮质量退化的根因之一。
