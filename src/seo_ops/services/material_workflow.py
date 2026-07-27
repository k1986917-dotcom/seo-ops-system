from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit

from seo_ops.config import Settings, get_settings
from seo_ops.db import connection
from seo_ops.ingest.snapshot import save_snapshot
from seo_ops.utils import json_dumps, json_loads, utc_now


class MaterialWorkflowError(ValueError):
    """Raised when a content-production material transition is not allowed."""


PUBLIC_URL_RE = re.compile(r"https?://[^\s<>()\]\[\"']+")
QUESTION_RE = re.compile(r"[^\n.!?]{8,180}\?", re.MULTILINE)
STOPWORDS = {
    "about",
    "after",
    "best",
    "from",
    "guide",
    "have",
    "laser",
    "pointer",
    "that",
    "the",
    "this",
    "under",
    "what",
    "when",
    "with",
}
MATERIAL_CATEGORIES = (
    ("A", "用户痛点", True),
    ("C", "案例素材", False),
    ("D", "信息增量", False),
    ("E", "权威引用", True),
    ("F", "竞品内容分析", False),
    ("G", "真实搜索问题", True),
    ("H", "作者与经验边界", False),
)


def _clip_text(value: Any, limit: int = 360) -> str:
    compact = " ".join(str(value or "").split())
    return compact[:limit]


def _public_urls(value: Any) -> set[str]:
    urls: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            urls.update(_public_urls(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            urls.update(_public_urls(item))
    elif isinstance(value, str):
        for candidate in PUBLIC_URL_RE.findall(value):
            cleaned = candidate.rstrip(".,;:!?'")
            parsed = urlsplit(cleaned)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                urls.add(cleaned)
    return urls


def _source_role(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    official_suffixes = (
        ".gov",
        ".gov.uk",
        ".gc.ca",
        ".edu",
        ".ac.uk",
        ".europa.eu",
        ".int",
    )
    if host in {"iso.org", "iec.ch"} or host.endswith(official_suffixes):
        return "official_or_research"
    community_domains = {
        "reddit.com",
        "quora.com",
        "facebook.com",
        "instagram.com",
        "tiktok.com",
        "x.com",
        "youtube.com",
    }
    if any(host == domain or host.endswith(f".{domain}") for domain in community_domains):
        return "community_signal"
    return "industry_or_other"


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())
        if len(token) >= 3 and token not in STOPWORDS
    }


def _flatten_text(value: Any, *, limit: int = 12) -> list[str]:
    output: list[str] = []

    def visit(item: Any) -> None:
        if len(output) >= limit:
            return
        if isinstance(item, dict):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)
        elif isinstance(item, (str, int, float)):
            text = _clip_text(item)
            if len(text) >= 8 and text not in output and not text.startswith("http"):
                output.append(text)

    visit(value)
    return output


def _values_for_keys(value: Any, keys: set[str], *, limit: int = 12) -> list[str]:
    output: list[str] = []

    def visit(item: Any) -> None:
        if len(output) >= limit:
            return
        if isinstance(item, dict):
            for key, nested in item.items():
                if str(key).casefold() in keys:
                    for text in _flatten_text(nested, limit=limit - len(output)):
                        if text not in output:
                            output.append(text)
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return output[:limit]


def _parse_manual_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {key: [] for key, _, _ in MATERIAL_CATEGORIES}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^[#>*\-\s]*([A-Ha-h])(?:[.、:：)\-]|\s)+", line)
        if match:
            current = match.group(1).upper()
            remainder = line[match.end() :].strip(" -:：")
            if remainder:
                sections[current].append(_clip_text(remainder))
            continue
        if current:
            sections[current].append(_clip_text(line))
    return {key: list(dict.fromkeys(values))[:12] for key, values in sections.items() if values}


def _article_tier(topic: str) -> tuple[str, int, int]:
    tokens = _tokens(topic)
    if tokens & {"best", "buy", "compare", "comparison", "review", "reviews", "versus"}:
        return "Product Roundup", 2000, 3500
    lowered = topic.casefold()
    if any(
        marker in lowered for marker in ("complete guide", "ultimate guide", "comprehensive guide")
    ):
        return "Pillar Page", 2500, 4000
    return "Cluster Content", 1200, 2500


def _is_sensitive_topic(topic: str) -> bool:
    return bool(
        _tokens(topic)
        & {
            "class",
            "eye",
            "legal",
            "law",
            "power",
            "radiation",
            "regulation",
            "safety",
            "sensor",
            "standard",
            "wavelength",
        }
    )


def _load_action_material(action_id: int, settings: Settings) -> dict[str, Any]:
    with connection(settings) as conn:
        row = conn.execute(
            """
            SELECT a.*, o.title opportunity_title, o.recommended_action,
                   o.evidence_json, o.gate_reasons_json, s.slug site_slug
            FROM actions a
            JOIN opportunities o ON o.id = a.opportunity_id
            JOIN sites s ON s.id = a.site_id
            WHERE a.id = ? AND a.decision = 'accepted'
            """,
            (action_id,),
        ).fetchone()
        if not row:
            raise MaterialWorkflowError("文章任务不存在")
        existing = conn.execute(
            "SELECT id FROM content_items WHERE site_id = ? AND canonical_url = ?",
            (row["site_id"], row["target_ref"]),
        ).fetchone()

        evidence = json_loads(row["evidence_json"], {})
        evidence_ids = [str(item) for item in evidence.get("evidence_ids", []) if item]
        step_rows = conn.execute(
            "SELECT step_key, status, evidence_refs_json FROM action_steps WHERE action_id = ?",
            (action_id,),
        ).fetchall()
        confirmed = False
        for step in step_rows:
            refs = json_loads(step["evidence_refs_json"], [])
            evidence_ids.extend(str(item) for item in refs if item)
            if step["step_key"] == "verify_primary_sources" and step["status"] == "done":
                confirmed = True
        evidence_ids = list(dict.fromkeys(evidence_ids))

        evidence_rows = []
        if evidence_ids:
            marks = ",".join("?" for _ in evidence_ids)
            evidence_rows = conn.execute(
                f"""
                SELECT evidence_id, evidence_type, source_ref, payload_json,
                       limitations_json, captured_at
                FROM evidence_items
                WHERE site_id = ? AND evidence_id IN ({marks})
                ORDER BY captured_at, evidence_id
                """,
                (row["site_id"], *evidence_ids),
            ).fetchall()

        content_rows = conn.execute(
            """
            SELECT ci.title, ci.slug, ci.canonical_url, ci.content_type, cs.summary
            FROM content_items ci
            LEFT JOIN content_snapshots cs ON cs.id = (
                SELECT id FROM content_snapshots
                WHERE content_item_id = ci.id ORDER BY captured_at DESC, id DESC LIMIT 1
            )
            WHERE ci.site_id = ? AND ci.status = 'active'
            ORDER BY ci.content_type, ci.id
            """,
            (row["site_id"],),
        ).fetchall()
    return {
        "task": dict(row),
        "evidence": evidence,
        "evidence_ids": evidence_ids,
        "evidence_rows": [dict(item) for item in evidence_rows],
        "content_rows": [dict(item) for item in content_rows],
        "confirmed": confirmed,
        "is_existing_article": bool(existing),
    }


def _select_internal_candidates(rows: list[dict[str, Any]], topic: str) -> list[dict[str, Any]]:
    topic_tokens = _tokens(topic)
    commercial = bool(topic_tokens & {"buy", "choice", "choose", "compare", "product"})
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for row in rows:
        url = str(row.get("canonical_url") or "")
        if not url:
            continue
        score = 4 * len(topic_tokens & _tokens(row.get("title")))
        score += len(topic_tokens & _tokens(row.get("summary")))
        if commercial and row.get("content_type") == "product":
            score += 2
        item = {
            "title": row.get("title"),
            "slug": row.get("slug"),
            "canonical_url": url,
            "content_type": row.get("content_type"),
            "summary": _clip_text(row.get("summary")),
        }
        scored.append((score, str(row.get("title") or "").casefold(), item))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item for score, _, item in scored if score > 0][:12]


def build_material_preview(
    action_id: int,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Organize already-stored evidence without network or AI calls."""

    active = settings or get_settings()
    loaded = _load_action_material(action_id, active)
    task = loaded["task"]
    evidence = loaded["evidence"]
    topic = " ".join(
        str(item or "")
        for item in (
            task.get("opportunity_title"),
            task.get("recommended_action"),
            task.get("target_ref"),
        )
    ).strip()
    tier, word_min, word_max = _article_tier(topic)
    sensitive = _is_sensitive_topic(topic)
    internal_candidates = _select_internal_candidates(loaded["content_rows"], topic)
    internal_urls = {str(item.get("canonical_url") or "") for item in loaded["content_rows"]}

    materials: list[dict[str, Any]] = []
    source_inventory: dict[str, dict[str, Any]] = {}
    manual_sections: dict[str, list[str]] = {}
    for row in loaded["evidence_rows"]:
        payload = json_loads(row["payload_json"], {})
        source_ref = str(row.get("source_ref") or "")
        urls = sorted(_public_urls({"source_ref": source_ref, "payload": payload}))
        item = {
            "evidence_id": str(row["evidence_id"]),
            "evidence_type": str(row["evidence_type"]),
            "source_ref": source_ref,
            "captured_at": row["captured_at"],
            "payload": payload,
            "limitations": json_loads(row["limitations_json"], []),
            "source_urls": urls,
        }
        materials.append(item)
        if item["evidence_type"] == "operator_material":
            for key, values in (payload.get("sections") or {}).items():
                manual_sections.setdefault(str(key).upper(), []).extend(_flatten_text(values))
        for url in urls:
            if url in internal_urls:
                continue
            record = source_inventory.setdefault(
                url,
                {"url": url, "source_role": _source_role(url), "evidence_ids": []},
            )
            if item["evidence_id"] not in record["evidence_ids"]:
                record["evidence_ids"].append(item["evidence_id"])

    for url in sorted(_public_urls(evidence)):
        if url in internal_urls:
            continue
        record = source_inventory.setdefault(
            url,
            {"url": url, "source_role": _source_role(url), "evidence_ids": []},
        )
        for evidence_id in loaded["evidence_ids"]:
            if evidence_id not in record["evidence_ids"]:
                record["evidence_ids"].append(evidence_id)

    facts = _values_for_keys(
        evidence,
        {"facts", "pain_points", "pain_point", "problems", "insights", "key_findings"},
    )
    for item in materials:
        facts.extend(
            _values_for_keys(
                item["payload"],
                {"facts", "pain_points", "pain_point", "problems", "insights", "key_findings"},
                limit=6,
            )
        )
    category_a = list(dict.fromkeys([*manual_sections.get("A", []), *facts]))[:12]

    cases: list[str] = list(manual_sections.get("C", []))
    for item in materials:
        roles = {_source_role(url) for url in item["source_urls"]}
        if "community_signal" in roles or item["evidence_type"] == "page_capture":
            cases.extend(
                _values_for_keys(
                    item["payload"],
                    {"content", "markdown", "text", "excerpt", "snippet", "description"},
                    limit=4,
                )
            )
    category_c = list(dict.fromkeys(cases))[:10]

    gains = _values_for_keys(
        evidence,
        {"inference", "inferences", "program_inference", "recommended_next_step", "rationale"},
    )
    recommended = _clip_text(task.get("recommended_action"))
    if recommended:
        gains.insert(0, recommended)
    category_d = list(dict.fromkeys([*manual_sections.get("D", []), *gains]))[:10]

    role_order = {"official_or_research": 0, "industry_or_other": 1, "community_signal": 2}
    sources = sorted(
        source_inventory.values(),
        key=lambda item: (role_order.get(str(item["source_role"]), 3), str(item["url"])),
    )
    authority_sources = [item for item in sources if item["source_role"] != "community_signal"]
    category_e = list(
        dict.fromkeys([*manual_sections.get("E", []), *[item["url"] for item in authority_sources]])
    )[:12]

    competitor: list[str] = list(manual_sections.get("F", []))
    for item in materials:
        if item["evidence_type"] in {"serp_snapshot", "source_discovery"}:
            competitor.extend(
                _values_for_keys(
                    item["payload"],
                    {"organic_results", "results", "competitors", "titles", "title"},
                    limit=6,
                )
            )
    category_f = list(dict.fromkeys(competitor))[:10]

    questions: list[str] = list(manual_sections.get("G", []))
    question_keys = {"related_questions", "people_also_ask", "paa", "questions", "question"}
    questions.extend(_values_for_keys(evidence, question_keys))
    for item in materials:
        questions.extend(_values_for_keys(item["payload"], question_keys, limit=8))
        if item["evidence_type"] == "operator_material":
            raw = str(item["payload"].get("raw_text") or "")
            questions.extend(_clip_text(match.group(0), 180) for match in QUESTION_RE.finditer(raw))
    category_g = [item for item in dict.fromkeys(questions) if item.rstrip().endswith("?")][:12]

    category_h = list(manual_sections.get("H", []))
    category_h.append("不使用固定化名；没有可追溯的真实经验时，禁止声称第一手实测或使用经历。")
    category_h = list(dict.fromkeys(category_h))[:8]

    category_values = {
        "A": category_a,
        "C": category_c,
        "D": category_d,
        "E": category_e,
        "F": category_f,
        "G": category_g,
        "H": category_h,
    }
    categories = [
        {
            "key": key,
            "label": label,
            "required": required,
            "items": category_values[key],
            "count": len(category_values[key]),
            "status": "ready" if category_values[key] else "missing",
        }
        for key, label, required in MATERIAL_CATEGORIES
    ]

    missing: list[str] = []
    if not category_a:
        missing.append("A 用户痛点")
    if not category_e:
        missing.append("E 权威引用")
    if not category_g:
        missing.append("G 真实搜索问题")
    if len(sources) < 2:
        missing.append("至少 2 个可核实来源")
    if sensitive and not any(item["source_role"] == "official_or_research" for item in sources):
        missing.append("安全/法规/技术主题的官方或研究来源")
    ready = not missing
    missing_keys = {item[0] for item in missing if item and item[0] in "ABCDEFGH"}
    prompt_lines = [
        f"请围绕文章任务“{task.get('opportunity_title') or task.get('target_ref')}”补充搜索素材。",
        "只粘贴你实际查到的内容，并在每条后面保留公开来源 URL；不要让 AI 编造经历、数字或出处。",
        "请按下面标题整理：",
    ]
    requested = missing_keys or {"A", "E", "G"}
    labels = {key: label for key, label, _ in MATERIAL_CATEGORIES}
    for key in ("A", "E", "G"):
        if key in requested:
            hint = {
                "A": "用户真正遇到的问题、限制或失败情形",
                "E": "官方、研究机构或可信行业一手来源及它能支持的事实",
                "G": "Google/PAA/论坛中出现的真实问句（保留问号）",
            }[key]
            prompt_lines.append(f"{key} {labels[key]}：{hint} + URL")
    prompt_lines.append(
        "可选：C 案例、D 信息增量、F 竞品缺口、H 可核实作者经验。不要补市场规模、价格或竞品报价。"
    )

    return {
        "action_id": action_id,
        "site_id": int(task["site_id"]),
        "opportunity_id": int(task["opportunity_id"]),
        "site_slug": str(task["site_slug"]),
        "is_existing_article": bool(loaded["is_existing_article"]),
        "topic": str(task.get("opportunity_title") or task.get("target_ref") or ""),
        "recommended_action": str(task.get("recommended_action") or ""),
        "tier": tier,
        "word_min": word_min,
        "word_max": word_max,
        "sensitive": sensitive,
        "categories": categories,
        "category_values": category_values,
        "sources": sources,
        "source_count": len(sources),
        "internal_candidates": internal_candidates,
        "evidence_ids": loaded["evidence_ids"],
        "materials": materials,
        "ready": ready,
        "confirmed": bool(loaded["confirmed"] and ready),
        "missing": missing,
        "search_prompt": "\n".join(prompt_lines),
    }


def save_manual_material(
    action_id: int,
    text: str,
    settings: Settings | None = None,
) -> str:
    """Store operator-pasted material as immutable, zero-API evidence."""

    active = settings or get_settings()
    cleaned = text.strip()
    if len(cleaned) < 20:
        raise MaterialWorkflowError("请粘贴实际搜索素材和来源链接")
    urls = sorted(_public_urls(cleaned))
    if not urls:
        raise MaterialWorkflowError("手工素材至少要包含一个可打开的 http(s) 来源链接")
    preview = build_material_preview(action_id, active)
    request_hash = hashlib.sha256(f"{action_id}\n{cleaned}".encode()).hexdigest()
    with connection(active) as conn:
        duplicate = conn.execute(
            """
            SELECT er.id, ei.evidence_id
            FROM external_runs er
            JOIN evidence_items ei ON ei.external_run_id = er.id
            WHERE er.site_id = ? AND er.opportunity_id = ? AND er.provider = 'operator'
              AND er.purpose = 'manual_material' AND er.request_sha256 = ?
              AND er.status = 'success'
            ORDER BY er.id DESC LIMIT 1
            """,
            (preview["site_id"], preview["opportunity_id"], request_hash),
        ).fetchone()
        if duplicate:
            return "这份手工素材已经保存过，不会重复写入"

    snapshot = save_snapshot(
        active,
        preview["site_slug"],
        "operator-material",
        f"action-{action_id}-manual-material.txt",
        cleaned.encode("utf-8"),
    )
    now = utc_now()
    with connection(active) as conn:
        cursor = conn.execute(
            """
            INSERT INTO external_runs(
                site_id, opportunity_id, provider, purpose, request_sha256,
                input_refs_json, parameters_json, status, response_snapshot_path,
                response_sha256, response_size, usage_json, started_at, completed_at
            ) VALUES(?, ?, 'operator', 'manual_material', ?, ?, ?, 'success', ?, ?, ?, ?, ?, ?)
            """,
            (
                preview["site_id"],
                preview["opportunity_id"],
                request_hash,
                json_dumps(preview["evidence_ids"]),
                json_dumps({"action_id": action_id, "source_urls": urls}),
                str(snapshot.path),
                snapshot.sha256,
                snapshot.size,
                json_dumps({"requests_attempted": 0, "billable": False, "source": "operator"}),
                now,
                now,
            ),
        )
        run_id = int(cursor.lastrowid)
        evidence_id = f"external:{run_id}:operator_material"
        conn.execute(
            """
            INSERT INTO evidence_items(
                evidence_id, site_id, external_run_id, evidence_type, evidence_level,
                source_ref, payload_json, limitations_json, captured_at
            ) VALUES(?, ?, ?, 'operator_material', 'E', 'operator-pasted-search', ?, ?, ?)
            """,
            (
                evidence_id,
                preview["site_id"],
                run_id,
                json_dumps(
                    {
                        "action_id": action_id,
                        "raw_text": cleaned,
                        "sections": _parse_manual_sections(cleaned),
                        "source_urls": urls,
                    }
                ),
                json_dumps(
                    [
                        "这是运营者粘贴的搜索素材；写作前仍须回到原始 URL 核对事实。",
                        "该记录未调用外部搜索 API 或 AI。",
                    ]
                ),
                now,
            ),
        )
        step_rows = conn.execute(
            "SELECT id, step_key, evidence_refs_json FROM action_steps WHERE action_id = ?",
            (action_id,),
        ).fetchall()
        for step in step_rows:
            refs = json_loads(step["evidence_refs_json"], [])
            if evidence_id not in refs:
                refs.append(evidence_id)
            conn.execute(
                "UPDATE action_steps SET evidence_refs_json = ?, updated_at = ? WHERE id = ?",
                (json_dumps(refs), now, step["id"]),
            )
        conn.execute(
            """
            UPDATE action_steps SET status = 'pending', completed_at = NULL, updated_at = ?
            WHERE action_id = ? AND step_key IN ('build_research_brief','verify_primary_sources')
            """,
            (now, action_id),
        )
    return "手工素材已加入；系统已重新整理素材缺口，没有调用 API"


def confirm_materials(
    action_id: int,
    settings: Settings | None = None,
) -> dict[str, Any]:
    active = settings or get_settings()
    preview = build_material_preview(action_id, active)
    if not preview["ready"]:
        raise MaterialWorkflowError("素材还缺少：" + "、".join(preview["missing"]))
    now = utc_now()
    with connection(active) as conn:
        conn.execute(
            """
            UPDATE action_steps
            SET status = 'done', completed_at = COALESCE(completed_at, ?), updated_at = ?
            WHERE action_id = ? AND step_key IN ('build_research_brief','verify_primary_sources')
            """,
            (now, now, action_id),
        )
        conn.execute("UPDATE actions SET updated_at = ? WHERE id = ?", (now, action_id))
    preview["confirmed"] = True
    return preview
