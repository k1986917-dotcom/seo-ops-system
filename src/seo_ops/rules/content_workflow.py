from __future__ import annotations

OLD_ARTICLE_CONTENT_QUALITY_RULE = {
    "rule_key": "old_article_content_quality",
    "version": "0.8.0",
    "rule_type": "governance",
    "evidence_level": "A+C+operator_policy",
    "rationale": (
        "GSC 页面异常只决定是否值得诊断，真实 query + page 行才决定读者任务。"
        "旧文章必须保持原主题、URL 和 slug，先起草再独立审校，并通过英语、篇幅、"
        "来源、链接与虚构声明的确定性检查后才能保存。"
    ),
    "config": {
        "reader_language": "en",
        "allowed_change_types": [
            "metadata_only",
            "partial_update",
            "same_topic_rewrite",
        ],
        "preserve_slug": True,
        "require_query_page_evidence": True,
        "require_draft_then_editor_review": True,
        "partial_update_min_words": 40,
        "same_topic_rewrite_min_words": 700,
        "same_topic_rewrite_min_existing_ratio": 0.7,
        "same_topic_rewrite_min_h2": 3,
        "seo_title_guidance_chars": [50, 60],
        "seo_description_guidance_chars": [150, 160],
        "metadata_length_is_blocking": False,
        "ai_call_limit": [3, 10],
        "approved_urls_only": True,
        "block_unverified_first_person_experience": True,
    },
    "sources": [
        "https://developers.google.com/search/docs/fundamentals/creating-helpful-content",
        "https://developers.google.com/webmaster-tools/v1/searchanalytics/query",
    ],
    "known_failures": [
        "通过结构检查不等于文章事实已由运营者最终核实",
        "GSC 顶部行与匿名查询限制可能让低量页面保持只诊断状态",
        "英文词数是局部结构下限；SEO 元数据字符区间仅为编辑建议，不是 Google 规则",
        "品牌名或合法专有名词需要人工复核，不能仅靠语言检测判断",
    ],
    "valid_from": "2026-07-17",
    "review_after": "2026-10-17",
}
