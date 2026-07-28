# ADR-0012：Legacy action/attempt 独立工作区

- 状态：Accepted
- 日期：2026-07-28
- 适用范围：新文章 Legacy Research + Write 通道

## 背景

旧 Skill 以主题 slug 和日期命名 Research、素材包、草稿、报告与 W2 状态。
新系统允许多个 action 存在，也允许同一 action 重试。若它们共用一个目录，
`latest file` 查找、同日覆盖和相同 slug 会让本次运行读取其他任务或上次尝试的产物。

## 决定

1. 每次从 R0 开始时，为该 action 创建新的 attempt 和不可重复 run ID。
2. 运行目录固定为
   `runs/action-{id}/attempt-{number}-{run_id}/laserpointerhub/`。
3. `research/`、`material-packs/`、`drafts/` 和 `reports/` 只属于该 attempt；
   当前 action 的后续路由只能解析其 `current.json` 指向的目录。
4. 每个 attempt 保存 manifest，登记 action、attempt、run ID、原始主题、规范 slug、
   工作区相对路径和创建时间。主题或 action 不匹配时拒绝复用。
5. `context/`、`published/` 和 `products/` 链接到 R0 开始前同步的共享事实快照；
   旧脚本仍看到原有同构目录，不修改 `/home/laoma/seo-workflow`。
6. 所有组件共用 `seo_common.slugify`。纯非拉丁主题使用 SHA-256 短摘要，
   禁止使用跨进程不稳定的 Python `hash()`。
7. 每次脚本报告使用 UTC 微秒时间和随机后缀保存，不覆盖同日旧报告。
8. collect 重试前只清除当前 attempt 当日的派生 data 文件，并要求本次命令同时
   生成 Markdown 与 JSON；脚本失败不能借用旧文件显示成功。
9. DB → published 同步后删除不再属于 active 快照的 Markdown，避免失效文章继续
   进入蚕食检查。

## 后果

同主题并行任务、同一任务跨日重试和脚本失败重试不会互相拿错产物。旧 attempt
保留用于审计；重新点击 R0 会明确创建新 attempt，而不是覆盖历史。

运行目录数量会随重试增加，后续如需清理只能新增保留策略，不能在当前流程中
静默删除历史。register 的多文件事务和并发写保护不属于本 ADR，留在下一批处理。

## 失效与复查条件

- 运行环境不支持目录符号链接。
- Legacy 脚本新增对工作区外绝对路径的依赖。
- register 改为数据库事务并不再使用共享 context。
- 运行历史增长到需要明确的归档/保留期限。

计划复查日期：2026-10-28。
