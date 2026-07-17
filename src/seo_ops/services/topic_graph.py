from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from seo_ops.config import Settings, get_settings
from seo_ops.db import connection
from seo_ops.utils import utc_now


@dataclass(frozen=True, slots=True)
class TopicGraphSummary:
    node_count: int
    mapped_content_count: int
    primary_count: int
    auxiliary_count: int
    unmapped_content_count: int


BASE_TOPICS = (
    (
        "laser-pointers",
        None,
        "激光笔",
        "root",
        "LaserPointerHub 已确认的内容边界。",
    ),
    ("use-cases", "laser-pointers", "使用场景", "branch", "用户在真实情境中要完成的任务。"),
    ("use-astronomy", "use-cases", "天文与观星", "branch", "指星、观星和夜间天空演示。"),
    ("use-outdoor", "use-cases", "户外、求生与紧急信号", "branch", "露营、徒步、求生和 SOS。"),
    ("use-presentations", "use-cases", "演示与教学", "branch", "会议、课堂和屏幕指示。"),
    ("use-photography", "use-cases", "摄影与光绘", "branch", "光绘和摄影创作。"),
    ("use-fishing-birds", "use-cases", "钓鱼与鸟类驱赶", "branch", "钓鱼环境中的鸟类问题。"),
    ("use-pets", "use-cases", "宠物互动", "branch", "猫等宠物相关使用和风险。"),
    ("use-professional", "use-cases", "工程与专业工作", "branch", "施工、园林和专业指示。"),
    ("buying", "laser-pointers", "选择与购买", "branch", "购买决策、预算、卖家和规格核验。"),
    ("buy-budget", "buying", "预算选择", "branch", "不同预算下的真实选择。"),
    ("buy-fit", "buying", "用途与性能选择", "branch", "按需求选择功率、颜色和形态。"),
    ("buy-quality", "buying", "质量、卖家与规格验证", "branch", "识别虚假规格和购买风险。"),
    ("buy-models", "buying", "型号、类型与评测", "branch", "具体型号、结构和类型差异。"),
    ("performance", "laser-pointers", "性能与原理", "branch", "光学、电学、热管理和测量。"),
    ("tech-power", "performance", "功率、mW 与测量", "branch", "真实输出、测量和功率含义。"),
    ("tech-wavelength", "performance", "波长、颜色与可见性", "branch", "波长和人眼可见性的关系。"),
    ("tech-optics", "performance", "光束、透镜与发散", "branch", "聚焦、光束质量和光学部件。"),
    ("tech-electronics", "performance", "二极管、驱动与工作原理", "branch", "发光器件和驱动电路。"),
    ("tech-thermal", "performance", "散热、材料与可靠性", "branch", "热设计、外壳和可靠性。"),
    ("operation", "laser-pointers", "操作、维护与故障", "branch", "日常使用、保养和配件。"),
    ("ops-cleaning", "operation", "清洁与光束故障", "branch", "脏污、模糊和散斑处理。"),
    ("ops-battery", "operation", "电池与充电", "branch", "电池兼容、充电和电压问题。"),
    ("ops-storage", "operation", "收纳、携带与保护", "branch", "储存、湿度和运输保护。"),
    ("ops-accessories", "operation", "支架、附件与光束工具", "branch", "支架、衍射帽和其他附件。"),
    ("ops-lifespan", "operation", "寿命、占空比与维护", "branch", "过热、损耗和维护周期。"),
    ("safety-law", "laser-pointers", "安全与法规", "branch", "眼睛安全、等级、标签和地区规则。"),
    ("safety-eye", "safety-law", "眼睛与人员安全", "branch", "眼损伤、儿童和旁观者风险。"),
    ("safety-class", "safety-law", "等级、标签与防护装备", "branch", "激光等级、标签和护目镜。"),
    ("safety-laws", "safety-law", "法律与地区规则", "branch", "拥有、购买和使用限制。"),
    ("safety-travel", "safety-law", "旅行与航空安全", "branch", "携带、航空和海关。"),
    ("products", "laser-pointers", "产品与型号证据", "branch", "已同步产品和具体型号事实。"),
    ("product-models", "products", "当前产品", "branch", "CMS 中当前可关联的产品页面。"),
    (
        "support-knowledge",
        "laser-pointers",
        "通用支撑知识",
        "branch",
        "可以出现在多篇文章中、但不自动构成重复主题的知识。",
    ),
    ("knowledge-safety", "support-knowledge", "安全", "knowledge", "常见安全提醒。"),
    ("knowledge-power", "support-knowledge", "功率", "knowledge", "常见功率说明。"),
    ("knowledge-wavelength", "support-knowledge", "波长与颜色", "knowledge", "常见波长说明。"),
    ("knowledge-battery", "support-knowledge", "电池与供电", "knowledge", "常见供电说明。"),
    ("knowledge-law", "support-knowledge", "法规", "knowledge", "常见法规说明。"),
)


PRIMARY_RULES = (
    ("use-fishing-birds", ("fishing", "bird deterr", "deter birds")),
    ("use-astronomy", ("astronomy", "stargaz", "star pointing")),
    ("use-presentations", ("presentation", "classroom")),
    ("use-photography", ("light painting", "photography")),
    ("use-pets", ("cats", "pets")),
    ("use-professional", ("construction", "landscaping", "professional use")),
    ("use-outdoor", ("outdoor", "camping", "hiking", "survival", "sos", "emergency signal")),
    ("safety-travel", ("flying", "carry", "airport", "airline")),
    ("safety-laws", ("laws by country", "legal", "regulation")),
    ("safety-class", ("class 3r", "class 4", "safety label", "safety glasses")),
    (
        "safety-eye",
        ("blind you", "eye damage", "hidden dangers", "complete guide to laser pointer safety"),
    ),
    ("ops-cleaning", ("cleaning", "dim, blurry", "speckled beam")),
    ("ops-battery", ("battery", "charger", "charge 18650", "voltage affect")),
    ("ops-storage", ("case & storage", "storage guide", "travel protection")),
    ("ops-accessories", ("mounts", "brackets", "accessories", "diffraction caps", "pattern heads")),
    ("ops-lifespan", ("lifespan", "duty cycle", "burn out", "maintenance")),
    ("buy-budget", ("under $", "budget laser")),
    (
        "buy-quality",
        ("quality verification", "build quality", "fake specs", "fake claims", "shipping, customs"),
    ),
    ("buy-models", ("301 vs 303", "g019", "laser sword review", "laser pointer types")),
    ("tech-optics", ("optics", "beam divergence", "focus")),
    ("tech-electronics", ("nichia", "driver circuits", "how does a high-power", "inside the beam")),
    (
        "tech-thermal",
        ("thermal design", "overheating", "copper vs", "stainless steel", "heat sink"),
    ),
    (
        "tech-wavelength",
        (
            "wavelength",
            "green vs",
            "blue vs green",
            "red vs green",
            "532nm",
            "520nm",
            "1064nm",
            "beam visibility",
        ),
    ),
    (
        "tech-power",
        (
            "power guide",
            "how much laser power",
            "test laser pointer power",
            "how powerful",
            "strongest",
            "1000mw",
            "5000mw",
            "50000mw",
            "burning",
        ),
    ),
    (
        "buy-fit",
        ("best laser pointer", "buying guide", "cheap vs expensive", "high power handheld"),
    ),
)


AUXILIARY_RULES = (
    ("knowledge-safety", ("safety", "danger", "eye", "class 4", "protect")),
    ("knowledge-power", (" mw", "power", "watt", "burning")),
    ("knowledge-wavelength", ("nm", "wavelength", "green laser", "blue laser", "red laser")),
    ("knowledge-battery", ("battery", "18650", "16340", "charge", "voltage")),
    ("knowledge-law", ("law", "legal", "regulation", "country", "airline", "customs")),
)


def _primary_parent(content_type: str, title: str, slug: str) -> tuple[str, str]:
    if content_type == "product":
        return "product-models", "product"
    text = f"{title} {slug}".casefold()
    for topic_key, patterns in PRIMARY_RULES:
        if any(pattern in text for pattern in patterns):
            return topic_key, "title_slug_rule"
    return "buy-fit", "fallback_review"


def _content_rows(settings: Settings, site_id: int) -> list[dict[str, Any]]:
    with connection(settings) as conn:
        rows = conn.execute(
            """
            SELECT ci.id, ci.content_type, ci.title, ci.slug, ci.canonical_url,
                   ci.status, cs.summary, cs.body
            FROM content_items ci
            LEFT JOIN content_snapshots cs ON cs.id = (
                SELECT latest.id FROM content_snapshots latest
                WHERE latest.content_item_id = ci.id
                ORDER BY latest.captured_at DESC, latest.id DESC LIMIT 1
            )
            WHERE ci.site_id = ?
            ORDER BY ci.content_type, ci.id
            """,
            (site_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _upsert_base_topics(conn: sqlite3.Connection, site_id: int, now: str) -> dict[str, int]:
    ids: dict[str, int] = {}
    pending = list(BASE_TOPICS)
    while pending:
        next_pending = []
        progressed = False
        for key, parent_key, name, node_type, description in pending:
            if parent_key is not None and parent_key not in ids:
                next_pending.append((key, parent_key, name, node_type, description))
                continue
            parent_id = ids.get(parent_key) if parent_key else None
            conn.execute(
                """
                INSERT INTO topic_nodes(
                    site_id, parent_id, topic_key, preferred_name, description,
                    node_type, display_status, boundary_status, source_type,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, 'no_opportunity', 'confirmed', 'system', ?, ?)
                ON CONFLICT(site_id, topic_key) DO UPDATE SET
                    parent_id = excluded.parent_id,
                    preferred_name = excluded.preferred_name,
                    description = excluded.description,
                    node_type = excluded.node_type,
                    updated_at = excluded.updated_at
                """,
                (site_id, parent_id, key, name, description, node_type, now, now),
            )
            ids[key] = int(
                conn.execute(
                    "SELECT id FROM topic_nodes WHERE site_id = ? AND topic_key = ?",
                    (site_id, key),
                ).fetchone()["id"]
            )
            progressed = True
        if not progressed:
            raise RuntimeError("主题骨架存在无法解析的父级")
        pending = next_pending
    return ids


def sync_topic_graph(site_id: int, settings: Settings | None = None) -> TopicGraphSummary:
    active_settings = settings or get_settings()
    content_rows = _content_rows(active_settings, site_id)
    now = utc_now()
    with connection(active_settings) as conn:
        legacy_candidates = conn.execute(
            """
            SELECT id FROM topic_nodes
            WHERE site_id = ? AND node_type = 'candidate' AND source_type = 'research'
            """,
            (site_id,),
        ).fetchall()
        if legacy_candidates:
            candidate_ids = [int(row["id"]) for row in legacy_candidates]
            marks = ",".join("?" for _ in candidate_ids)
            conn.execute(
                f"UPDATE topic_decisions SET topic_id = NULL WHERE topic_id IN ({marks})",
                candidate_ids,
            )
            conn.execute(
                f"DELETE FROM topic_nodes WHERE id IN ({marks})",
                candidate_ids,
            )
        topic_ids = _upsert_base_topics(conn, site_id, now)
        conn.execute(
            """
            DELETE FROM topic_content_links
            WHERE coverage_role IN ('primary', 'auxiliary') AND human_confirmed = 0
              AND content_item_id IN (SELECT id FROM content_items WHERE site_id = ?)
            """,
            (site_id,),
        )
        for item in content_rows:
            parent_key, basis = _primary_parent(
                str(item["content_type"]), str(item["title"]), str(item["slug"])
            )
            topic_key = f"content-{item['id']}"
            description = (
                str(item.get("summary") or "")[:500] or "根据当前 CMS 内容建立的主要文章主题。"
            )
            conn.execute(
                """
                INSERT INTO topic_nodes(
                    site_id, parent_id, topic_key, preferred_name, description,
                    node_type, display_status, boundary_status, source_type,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, 'article_topic', 'covered',
                         'confirmed', 'import', ?, ?)
                ON CONFLICT(site_id, topic_key) DO UPDATE SET
                    parent_id = excluded.parent_id,
                    preferred_name = excluded.preferred_name,
                    description = excluded.description,
                    display_status = 'covered',
                    updated_at = excluded.updated_at
                """,
                (
                    site_id,
                    topic_ids[parent_key],
                    topic_key,
                    item["title"],
                    description,
                    now,
                    now,
                ),
            )
            article_topic_id = int(
                conn.execute(
                    "SELECT id FROM topic_nodes WHERE site_id = ? AND topic_key = ?",
                    (site_id, topic_key),
                ).fetchone()["id"]
            )
            confirmed_primary = conn.execute(
                """
                SELECT id FROM topic_content_links
                WHERE content_item_id = ? AND coverage_role = 'primary'
                  AND human_confirmed = 1
                LIMIT 1
                """,
                (item["id"],),
            ).fetchone()
            if confirmed_primary:
                continue
            conn.execute(
                """
                INSERT INTO topic_content_links(
                    topic_id, content_item_id, coverage_role, mapping_basis,
                    confidence, human_confirmed, created_at, updated_at
                ) VALUES(?, ?, 'primary', ?, ?, 0, ?, ?)
                ON CONFLICT(topic_id, content_item_id, coverage_role) DO UPDATE SET
                    mapping_basis = excluded.mapping_basis,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                (
                    article_topic_id,
                    item["id"],
                    f"{basis}:{parent_key}",
                    "high" if basis != "fallback_review" else "medium",
                    now,
                    now,
                ),
            )

            searchable = " ".join(
                str(value or "") for value in (item["title"], item["slug"], item.get("body"))
            ).casefold()
            for knowledge_key, patterns in AUXILIARY_RULES:
                if not any(pattern in searchable for pattern in patterns):
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO topic_content_links(
                        topic_id, content_item_id, coverage_role, mapping_basis,
                        confidence, human_confirmed, created_at, updated_at
                    ) VALUES(?, ?, 'auxiliary', 'keyword_presence', 'medium', 0, ?, ?)
                    """,
                    (topic_ids[knowledge_key], item["id"], now, now),
                )

        nodes = conn.execute(
            "SELECT id, parent_id, node_type FROM topic_nodes WHERE site_id = ?", (site_id,)
        ).fetchall()
        parent_by_id = {int(row["id"]): row["parent_id"] for row in nodes}
        covered_ids = {
            int(row["topic_id"])
            for row in conn.execute(
                """
                SELECT DISTINCT l.topic_id FROM topic_content_links l
                JOIN content_items ci ON ci.id = l.content_item_id
                WHERE ci.site_id = ? AND l.coverage_role = 'primary'
                """,
                (site_id,),
            ).fetchall()
        }
        branch_covered = set(covered_ids)
        for topic_id in list(covered_ids):
            parent_id = parent_by_id.get(topic_id)
            while parent_id is not None:
                branch_covered.add(int(parent_id))
                parent_id = parent_by_id.get(int(parent_id))
        conn.execute(
            """
            UPDATE topic_nodes SET display_status = 'no_opportunity', updated_at = ?
            WHERE site_id = ? AND node_type IN ('root','branch')
            """,
            (now, site_id),
        )
        if branch_covered:
            marks = ",".join("?" for _ in branch_covered)
            conn.execute(
                f"UPDATE topic_nodes SET display_status = 'covered', updated_at = ? "
                f"WHERE site_id = ? AND id IN ({marks})",
                (now, site_id, *sorted(branch_covered)),
            )

        counts = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM topic_nodes WHERE site_id = ?) AS node_count,
                (SELECT COUNT(DISTINCT l.content_item_id)
                 FROM topic_content_links l JOIN content_items ci ON ci.id = l.content_item_id
                 WHERE ci.site_id = ?) AS mapped_content_count,
                (SELECT COUNT(*) FROM topic_content_links l
                 JOIN content_items ci ON ci.id = l.content_item_id
                 WHERE ci.site_id = ? AND l.coverage_role = 'primary') AS primary_count,
                (SELECT COUNT(*) FROM topic_content_links l
                 JOIN content_items ci ON ci.id = l.content_item_id
                 WHERE ci.site_id = ? AND l.coverage_role = 'auxiliary') AS auxiliary_count,
                (SELECT COUNT(*) FROM content_items WHERE site_id = ?) AS total_content
            """,
            (site_id, site_id, site_id, site_id, site_id),
        ).fetchone()
    return TopicGraphSummary(
        node_count=int(counts["node_count"]),
        mapped_content_count=int(counts["mapped_content_count"]),
        primary_count=int(counts["primary_count"]),
        auxiliary_count=int(counts["auxiliary_count"]),
        unmapped_content_count=int(counts["total_content"]) - int(counts["mapped_content_count"]),
    )


def topic_tree(site_id: int, settings: Settings | None = None) -> list[dict[str, Any]]:
    active_settings = settings or get_settings()
    with connection(active_settings) as conn:
        rows = conn.execute(
            """
            SELECT * FROM topic_nodes WHERE site_id = ?
            ORDER BY CASE node_type
                WHEN 'root' THEN 0 WHEN 'branch' THEN 1
                WHEN 'knowledge' THEN 2 ELSE 3 END,
                preferred_name, id
            """,
            (site_id,),
        ).fetchall()
        links = conn.execute(
            """
            SELECT l.topic_id, l.coverage_role, l.confidence, l.mapping_basis,
                   ci.id AS content_item_id, ci.title, ci.canonical_url, ci.content_type
            FROM topic_content_links l
            JOIN content_items ci ON ci.id = l.content_item_id
            WHERE ci.site_id = ?
            ORDER BY ci.title
            """,
            (site_id,),
        ).fetchall()
    nodes: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item["children"] = []
        item["articles"] = []
        item["auxiliary_count"] = 0
        item["article_count"] = 0
        nodes[int(row["id"])] = item
    for row in links:
        node = nodes.get(int(row["topic_id"]))
        if not node:
            continue
        if row["coverage_role"] == "primary":
            node["articles"].append(dict(row))
        else:
            node["auxiliary_count"] += 1
    roots: list[dict[str, Any]] = []
    for node in nodes.values():
        parent_id = node.get("parent_id")
        if parent_id is None or int(parent_id) not in nodes:
            roots.append(node)
        else:
            nodes[int(parent_id)]["children"].append(node)

    def aggregate(node: dict[str, Any]) -> int:
        count = len(node["articles"])
        for child in node["children"]:
            count += aggregate(child)
        node["article_count"] = count
        return count

    for root in roots:
        aggregate(root)
    return roots
