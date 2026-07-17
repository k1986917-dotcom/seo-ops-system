from __future__ import annotations

GSC_QUERY_PAGE_SYNC_RULE = {
    "rule_key": "gsc_query_page_sync",
    "version": "0.9.0",
    "rule_type": "official_fact",
    "evidence_level": "A+B",
    "rationale": (
        "查询表与页面表是不同聚合，不能靠关键词拼接。只读 OAuth 同步必须请求 "
        "query + page 联合维度，并把站点确认的可信起始日作为显式数据边界。"
    ),
    "config": {
        "scope": "https://www.googleapis.com/auth/webmasters.readonly",
        "data_state": "final",
        "search_type": "web",
        "row_limit": 25000,
        "default_trusted_start_date": "2026-06-22",
        "joint_dimensions": ["date", "query", "page"],
    },
    "sources": [
        "https://developers.google.com/webmaster-tools/v1/searchanalytics/query",
        "https://developers.google.com/identity/protocols/oauth2/native-app",
    ],
    "known_failures": [
        "匿名查询不会返回",
        "Search Analytics API 仍可能只返回顶部数据行",
        "OAuth 授权被撤销后必须重新连接",
        "未完成数据不得进入活动分析批次",
    ],
    "valid_from": "2026-07-17",
    "review_after": "2026-10-17",
}


OLD_ARTICLE_GSC_READINESS_RULE = {
    "rule_key": "old_article_gsc_readiness",
    "version": "0.9.0",
    "rule_type": "governance",
    "evidence_level": "A",
    "rationale": (
        "页面级点击、展示、CTR 或平均排名只能发现诊断对象。若内容动作由 GSC 表现触发，"
        "必须有该页面的真实 query + page 行，才能进入文章制作。"
    ),
    "config": {
        "gsc_driven_actions_require_query_page": True,
        "page_only_gate_status": "needs_evidence",
        "non_gsc_deterministic_fixes_may_proceed": True,
        "top_queries_in_brief": 10,
    },
    "sources": [
        "https://support.google.com/webmasters/answer/17011259?hl=en",
        "https://developers.google.com/webmaster-tools/v1/searchanalytics/query",
    ],
    "known_failures": [
        "存在联合行不等于已经证明内容质量问题",
        "低量页面可能因隐私和顶部行限制缺少查询行",
        "事实过期、失效链接等非 GSC 缺陷应走独立确定性规则",
    ],
    "valid_from": "2026-07-17",
    "review_after": "2026-10-17",
}
