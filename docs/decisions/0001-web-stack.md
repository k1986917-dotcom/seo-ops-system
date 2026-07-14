# ADR-0001：采用 FastAPI + Jinja + SQLite

- 状态：Accepted
- 日期：2026-07-14

## 背景

系统主要由一名运营者在 WSL 本地使用，需要读取 Excel/JSON、执行 Python 分析并提供网页界面。第一版不需要复杂多人协作或公网部署。

## 决策

- FastAPI 提供路由、文件上传、JSON API 和后续 AI 异步调用。
- Jinja + 原生 CSS/JS 提供单体网页，不创建独立 React/Node 工程。
- SQLite 保存结构化数据；原始文件保存在不可变快照目录。

## 结果

优点：安装与备份简单、Python 数据处理直接、单人维护成本低。限制：高并发、多用户权限和分布式任务需要以后迁移，但不属于当前需求。

