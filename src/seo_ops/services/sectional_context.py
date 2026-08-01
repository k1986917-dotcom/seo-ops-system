"""Site-agnostic shadow context registry and opportunity scoring.

The product catalog is the commercial source of truth. A Legacy brief may
suggest article angles, but its prose cannot silently become a product filter.
Only explicit structured constraints from an approved site/operator policy may
filter products. The parser supports any markdown catalog that exposes a
product ID, title/name and URL; all other columns remain generic attributes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from seo_ops.services.sectional_consistency import wavelength_color_conflicts
from seo_ops.services.sectional_writing import (
    CONTRACT_VERSION,
    ContractValidationError,
    contract_paths,
    validate_section_contracts,
    validate_section_link_contracts,
)

ARTICLE_CANDIDATE_LIMIT = 2
PRODUCT_CANDIDATE_LIMIT = 2
EVIDENCE_CANDIDATE_LIMIT = 3

_GENERIC_TOKENS = frozenset({
    "a",
    "about",
    "an",
    "and",
    "answer",
    "are",
    "article",
    "as",
    "at",
    "be",
    "best",
    "by",
    "can",
    "complete",
    "do",
    "does",
    "for",
    "from",
    "give",
    "guide",
    "how",
    "in",
    "into",
    "is",
    "of",
    "on",
    "or",
    "section",
    "that",
    "the",
    "this",
    "to",
    "use",
    "using",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "your",
})

_ARTICLE_TITLE_ALIASES = {"title", "article", "article_title", "name"}
_ARTICLE_URL_ALIASES = {"url", "link", "article_url"}
_ARTICLE_KEYWORD_ALIASES = {"primary_keyword", "keyword", "keywords", "topic"}
_PRODUCT_ID_ALIASES = {
    "sku",
    "id",
    "model",
    "model_number",
    "product_id",
    "product_code",
}
_PRODUCT_TITLE_ALIASES = {"title", "name", "product", "product_name"}
_PRODUCT_URL_ALIASES = {"url", "link", "product_url"}
_PRODUCT_NARRATIVE_FIELDS = {
    "description",
    "details",
    "features",
    "summary",
    "usage",
    "usage_scenario",
    "usage_scenarios",
}

_PRESCRIPTIVE_PATTERN = re.compile(
    r"(?i)\b(?:only|must|required|should only|recommend(?:ed|ation|ations)?)\b"
    r"|只推荐|必须|不得|仅限|唯一",
)
_SPEC_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9])\d+(?:\.\d+)?\s*[a-z%/]+(?![a-z0-9])"
)

_UNIT_FACTORS = {
    "w": ("power", 1.0),
    "mw": ("power", 0.001),
    "kw": ("power", 1000.0),
    "m": ("length", 1.0),
    "cm": ("length", 0.01),
    "mm": ("length", 0.001),
    "kg": ("mass", 1.0),
    "g": ("mass", 0.001),
    "l": ("volume", 1.0),
    "ml": ("volume", 0.001),
}


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _field_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")


def _normalized_token(value: str) -> str:
    token = value.casefold()
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if (
        len(token) > 4
        and token.endswith("s")
        and not token.endswith(("ss", "us", "is"))
    ):
        return token[:-1]
    return token


def _tokens(value: str) -> set[str]:
    return {
        normalized
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) >= 2
        if (normalized := _normalized_token(token)) not in _GENERIC_TOKENS
    }


def _spec_tokens(value: str) -> set[str]:
    return {
        re.sub(r"\s+", "", match.group(0).casefold())
        for match in _SPEC_PATTERN.finditer(value)
    }


def _specs_by_unit(value: str) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = {}
    for match in re.finditer(
        r"(?i)(?<![a-z0-9])(\d+(?:\.\d+)?)\s*([a-z%/]+)(?![a-z0-9])",
        value,
    ):
        raw_number = match.group(1)
        number = (
            raw_number.rstrip("0").rstrip(".")
            if "." in raw_number
            else raw_number
        )
        unit = match.group(2).casefold()
        grouped.setdefault(unit, set()).add(number)
    return grouped


def _valid_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _canonical_url(value: str) -> str:
    parsed = urlparse(value.strip())
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def _extract_url(value: str) -> str:
    clean = value.strip()
    markdown = re.fullmatch(r"\[[^\]]*\]\((https?://[^)]+)\)", clean)
    return markdown.group(1).strip() if markdown else clean


def _candidate_id(kind: str, identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"{kind}-{digest}"


def _markdown_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    tables: list[tuple[list[str], list[list[str]]]] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not (line.startswith("|") and line.endswith("|")):
            index += 1
            continue
        block: list[list[str]] = []
        while index < len(lines):
            current = lines[index].strip()
            if not (current.startswith("|") and current.endswith("|")):
                break
            block.append([cell.strip() for cell in current.strip("|").split("|")])
            index += 1
        if len(block) >= 2 and all(
            re.fullmatch(r":?-{3,}:?", cell or "") for cell in block[1]
        ):
            tables.append((block[0], block[2:]))
    return tables


def _find_column(header: list[str], aliases: set[str]) -> int | None:
    for index, value in enumerate(header):
        if _field_key(value) in aliases:
            return index
    return None


def _find_table(
    text: str,
    required_aliases: tuple[set[str], ...],
) -> tuple[list[str], list[list[str]]] | None:
    for header, rows in _markdown_tables(text):
        if all(_find_column(header, aliases) is not None for aliases in required_aliases):
            return header, rows
    return None


def parse_article_registry(
    internal_links_map: str,
    *,
    current_url: str = "",
    current_slug: str = "",
) -> dict[str, Any]:
    table = _find_table(
        internal_links_map,
        (_ARTICLE_TITLE_ALIASES, _ARTICLE_URL_ALIASES),
    )
    if table is None:
        return {"version": CONTRACT_VERSION, "candidates": [], "rejected": []}
    header, rows = table
    title_index = _find_column(header, _ARTICLE_TITLE_ALIASES)
    url_index = _find_column(header, _ARTICLE_URL_ALIASES)
    keyword_index = _find_column(header, _ARTICLE_KEYWORD_ALIASES)
    assert title_index is not None and url_index is not None

    canonical_current = _canonical_url(current_url) if _valid_url(current_url) else ""
    clean_slug = current_slug.strip("/ ").casefold()
    seen_urls: set[str] = set()
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for row in rows:
        if len(row) <= max(title_index, url_index):
            continue
        title = _clean_text(row[title_index])
        url = row[url_index].strip()
        keyword = (
            _clean_text(row[keyword_index])
            if keyword_index is not None and len(row) > keyword_index
            else ""
        )
        if not title or not _valid_url(url):
            rejected.append({"identity": url or title, "reason_code": "invalid_article_url"})
            continue
        canonical = _canonical_url(url)
        url_slug = urlparse(canonical).path.rstrip("/").split("/")[-1].casefold()
        if canonical == canonical_current or (clean_slug and url_slug == clean_slug):
            rejected.append({"identity": canonical, "reason_code": "self_link"})
            continue
        if canonical in seen_urls:
            rejected.append({"identity": canonical, "reason_code": "duplicate_article_url"})
            continue
        seen_urls.add(canonical)
        candidates.append({
            "candidate_id": _candidate_id("article", canonical),
            "title": title,
            "url": canonical,
            "primary_keyword": keyword,
            "search_text": _clean_text(f"{title} {keyword}"),
        })
    return {
        "version": CONTRACT_VERSION,
        "candidates": candidates,
        "rejected": rejected,
    }


def _product_detail_blocks(report: str) -> dict[str, dict[str, str]]:
    matches = list(re.finditer(r"(?m)^###\s+(.+?)\s+(?:—|–|-)\s+.*$", report))
    details: dict[str, dict[str, str]] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(report)
        fields = {
            _field_key(label): _clean_text(value)
            for label, value in re.findall(
                r"(?mi)^-\s+\*\*(.+?)\*\*\s*:\s*(.+?)\s*$",
                report[match.end():end],
            )
            if _field_key(label)
        }
        details[_clean_text(match.group(1))] = fields
    return details


def _attribute_equivalent(left: str, right: str) -> bool:
    def normalize(value: str) -> str:
        return re.sub(r"[\s$€£,]+", "", value.casefold())

    return normalize(left) == normalize(right)


def parse_product_registry(product_report: str) -> dict[str, Any]:
    """Parse a generic product table; no industry-specific columns are required."""
    table = _find_table(
        product_report,
        (_PRODUCT_ID_ALIASES, _PRODUCT_TITLE_ALIASES, _PRODUCT_URL_ALIASES),
    )
    if table is None:
        raise ContractValidationError(
            "product report needs ID/SKU, title/name and URL columns"
        )
    header, rows = table
    id_index = _find_column(header, _PRODUCT_ID_ALIASES)
    title_index = _find_column(header, _PRODUCT_TITLE_ALIASES)
    url_index = _find_column(header, _PRODUCT_URL_ALIASES)
    assert id_index is not None and title_index is not None and url_index is not None
    normalized_header = [_field_key(value) for value in header]
    details = _product_detail_blocks(product_report)
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for row in rows:
        if len(row) <= max(id_index, title_index, url_index):
            continue
        product_id = _clean_text(row[id_index])
        title = _clean_text(row[title_index])
        url = row[url_index].strip()
        if not product_id or not title or not _valid_url(url):
            rejected.append({
                "identity": product_id or url or title,
                "reason_code": "invalid_product_row",
            })
            continue
        canonical = _canonical_url(url)
        if canonical in seen_urls:
            rejected.append({"identity": canonical, "reason_code": "duplicate_product_url"})
            continue
        seen_urls.add(canonical)
        table_attributes = {
            key: _clean_text(row[index])
            for index, key in enumerate(normalized_header)
            if key
            and index < len(row)
            and index not in {id_index, title_index, url_index}
            and _clean_text(row[index])
        }
        detail_attributes = {
            key: value
            for key, value in details.get(product_id, {}).items()
            if key not in _PRODUCT_URL_ALIASES
        }
        conflicts = [
            {
                "field": key,
                "table": table_attributes[key],
                "detail": detail_attributes[key],
            }
            for key in sorted(table_attributes.keys() & detail_attributes.keys())
            if not _attribute_equivalent(table_attributes[key], detail_attributes[key])
        ]
        merged = dict(table_attributes)
        merged.update(detail_attributes)
        eligibility_attributes = {
            key: value
            for key, value in merged.items()
            if key not in _PRODUCT_NARRATIVE_FIELDS
        }
        title_specs = _specs_by_unit(title)
        for key, value in merged.items():
            conflicts.extend(
                {
                    "field": key,
                    "reason_code": "wavelength_color_mismatch",
                    **conflict,
                }
                for conflict in wavelength_color_conflicts(value)
            )
            attribute_specs = _specs_by_unit(value)
            for unit in sorted(title_specs.keys() & attribute_specs.keys()):
                if title_specs[unit].isdisjoint(attribute_specs[unit]):
                    conflicts.append({
                        "field": key,
                        "title": sorted(title_specs[unit]),
                        "attribute": sorted(attribute_specs[unit]),
                        "unit": unit,
                    })
        candidates.append({
            "candidate_id": _candidate_id("product", product_id.casefold()),
            "product_id": product_id,
            "title": title,
            "url": canonical,
            "in_stock": True,
            "attributes": {
                "table": table_attributes,
                "details": detail_attributes,
                "merged": merged,
            },
            "attribute_conflicts": conflicts,
            "eligibility_text": _clean_text(
                " ".join([title, product_id, *eligibility_attributes.values()])
            ),
            "search_text": _clean_text(
                " ".join([title, product_id, *table_attributes.values(), *detail_attributes.values()])
            ),
        })
    return {
        "version": CONTRACT_VERSION,
        "candidates": candidates,
        "rejected": rejected,
    }


def parse_evidence_registry(evidence_cards: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in evidence_cards.get("all_cards", []):
        if not isinstance(raw, dict):
            rejected.append({"identity": "", "reason_code": "invalid_evidence_card"})
            continue
        evidence_id = _clean_text(raw.get("evidence_id"))
        url = _extract_url(_clean_text(raw.get("source_url")))
        support = _clean_text(raw.get("support"))
        support_basis = _clean_text(raw.get("support_basis")) or "key_finding"
        if not evidence_id or evidence_id in seen or not support or not _valid_url(url):
            rejected.append({
                "identity": evidence_id or url,
                "reason_code": "invalid_or_duplicate_evidence_card",
            })
            continue
        if support_basis not in {"verified_quote", "quote", "key_finding"}:
            rejected.append({
                "identity": evidence_id,
                "reason_code": "invalid_evidence_support_basis",
            })
            continue
        seen.add(evidence_id)
        concepts = [_clean_text(item) for item in raw.get("concepts", []) if _clean_text(item)]
        claim_types = [
            _clean_text(item) for item in raw.get("claim_types", []) if _clean_text(item)
        ]
        candidates.append({
            "candidate_id": evidence_id,
            "evidence_id": evidence_id,
            "url": _canonical_url(url),
            "support": support,
            "support_basis": support_basis,
            "concepts": concepts,
            "claim_types": claim_types,
            "required": bool(raw.get("required")),
            "search_text": _clean_text(
                f"{support} {' '.join(concepts)} {' '.join(claim_types)}"
            ),
        })
    return {
        "version": CONTRACT_VERSION,
        "candidates": candidates,
        "rejected": rejected,
    }


def _common_candidate_tokens(
    candidates: list[dict[str, Any]],
    *,
    text_field: str = "search_text",
) -> list[str]:
    token_counts: dict[str, int] = {}
    for candidate in candidates:
        for token in _tokens(candidate.get(text_field, "")):
            token_counts[token] = token_counts.get(token, 0) + 1
    threshold = max(3, (len(candidates) * 3 + 4) // 5)
    return sorted(
        token
        for token, count in token_counts.items()
        if count >= threshold
    )


def build_catalog_profile(products: dict[str, Any]) -> dict[str, Any]:
    candidates = products.get("candidates", [])
    attribute_counts: dict[str, int] = {}
    catalog_specs: set[str] = set()
    for candidate in candidates:
        for key in candidate.get("attributes", {}).get("merged", {}):
            attribute_counts[key] = attribute_counts.get(key, 0) + 1
        catalog_specs.update(_spec_tokens(candidate.get("search_text", "")))
    return {
        "product_count": len(candidates),
        "attribute_coverage": dict(sorted(attribute_counts.items())),
        "catalog_spec_tokens": sorted(catalog_specs),
        "common_product_tokens": _common_candidate_tokens(
            candidates,
            text_field="eligibility_text",
        ),
        "conflicted_product_count": sum(
            bool(candidate.get("attribute_conflicts")) for candidate in candidates
        ),
    }


def build_catalog_data_issues(products: dict[str, Any]) -> list[dict[str, Any]]:
    """Return actionable product-data issues without changing catalog records."""
    issues: list[dict[str, Any]] = []
    for candidate in products.get("candidates", []):
        conflicts = candidate.get("attribute_conflicts") or []
        if not conflicts:
            continue
        product_id = candidate["product_id"]
        issue_identity = json.dumps(
            [product_id, conflicts],
            sort_keys=True,
            ensure_ascii=False,
        )
        issues.append({
            "issue_id": _candidate_id("catalog-issue", issue_identity),
            "severity": "error",
            "reason_code": "catalog_attribute_conflict",
            "product_id": product_id,
            "title": candidate["title"],
            "url": candidate["url"],
            "conflicts": conflicts,
            "blocking_for_auto_link": True,
            "suggested_action": (
                "Correct the product title, catalog table or product detail so "
                "the same attribute has one consistent value, then refresh the "
                "product report."
            ),
        })
    for rejected in products.get("rejected", []):
        issue_identity = json.dumps(rejected, sort_keys=True, ensure_ascii=False)
        issues.append({
            "issue_id": _candidate_id("catalog-issue", issue_identity),
            "severity": "error",
            "reason_code": rejected.get("reason_code", "invalid_product_record"),
            "product_id": rejected.get("identity", ""),
            "title": "",
            "url": "",
            "conflicts": [],
            "blocking_for_auto_link": True,
            "suggested_action": (
                "Correct the product ID, title or URL in the catalog source, "
                "then refresh the product report."
            ),
        })
    return issues


def build_candidate_registry(
    *,
    internal_links_map: str,
    evidence_cards: dict[str, Any],
    product_report: str | None = None,
    live_products_report: str | None = None,
    current_url: str = "",
    current_slug: str = "",
) -> dict[str, Any]:
    catalog_text = product_report if product_report is not None else live_products_report
    if catalog_text is None:
        raise ContractValidationError("product_report is required")
    products = parse_product_registry(catalog_text)
    articles = parse_article_registry(
        internal_links_map,
        current_url=current_url,
        current_slug=current_slug,
    )
    catalog_data_issues = build_catalog_data_issues(products)
    registry = {
        "version": CONTRACT_VERSION,
        "current_url": _canonical_url(current_url) if _valid_url(current_url) else "",
        "current_slug": current_slug.strip("/ "),
        "articles": articles,
        "article_profile": {
            "article_count": len(articles["candidates"]),
            "common_article_tokens": _common_candidate_tokens(
                articles["candidates"]
            ),
        },
        "products": products,
        "evidence": parse_evidence_registry(evidence_cards),
        "catalog_profile": build_catalog_profile(products),
        "catalog_data_issues": catalog_data_issues,
    }
    return validate_candidate_registry(registry)


def validate_candidate_registry(registry: Any) -> dict[str, Any]:
    if not isinstance(registry, dict) or registry.get("version") != CONTRACT_VERSION:
        raise ContractValidationError("candidate registry must be a version 1 object")
    if not isinstance(registry.get("catalog_profile"), dict):
        raise ContractValidationError("candidate registry catalog_profile is missing")
    article_profile = registry.get("article_profile")
    if (
        not isinstance(article_profile, dict)
        or not isinstance(article_profile.get("article_count"), int)
        or not isinstance(article_profile.get("common_article_tokens"), list)
    ):
        raise ContractValidationError("candidate registry article_profile is invalid")
    catalog_data_issues = registry.get("catalog_data_issues")
    if not isinstance(catalog_data_issues, list) or any(
        not isinstance(item, dict)
        or item.get("severity") != "error"
        or not isinstance(item.get("issue_id"), str)
        or not item.get("issue_id")
        or item.get("blocking_for_auto_link") is not True
        for item in catalog_data_issues
    ):
        raise ContractValidationError("candidate registry catalog_data_issues is invalid")
    for kind in ("articles", "products", "evidence"):
        group = registry.get(kind)
        if not isinstance(group, dict):
            raise ContractValidationError(f"candidate registry {kind} must be an object")
        candidates = group.get("candidates")
        rejected = group.get("rejected")
        if not isinstance(candidates, list) or not isinstance(rejected, list):
            raise ContractValidationError(f"candidate registry {kind} lists are malformed")
        ids = [candidate.get("candidate_id") for candidate in candidates]
        if any(not isinstance(item, str) or not item for item in ids):
            raise ContractValidationError(f"candidate registry {kind} candidate_id is invalid")
        if len(ids) != len(set(ids)):
            raise ContractValidationError(f"candidate registry {kind} IDs must be unique")
        if any(not isinstance(item, dict) for item in rejected):
            raise ContractValidationError(f"candidate registry {kind} rejected items are invalid")
    return registry


def _section_text(section: dict[str, Any]) -> str:
    return _clean_text(
        " ".join([
            section.get("heading", ""),
            section.get("reader_question", ""),
            section.get("section_goal", ""),
            " ".join(section.get("must_answer", [])),
        ])
    )


def _rank(
    candidates: list[dict[str, Any]],
    section: dict[str, Any],
    *,
    kind: str,
    topic: str = "",
    ignored_section_tokens: set[str] | None = None,
    text_field: str = "search_text",
) -> list[dict[str, Any]]:
    section_tokens = _tokens(_section_text(section)) - set(
        ignored_section_tokens or set()
    )
    topic_tokens = _tokens(topic)
    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        haystack_tokens = _tokens(candidate.get(text_field, ""))
        section_matches = sorted(section_tokens & haystack_tokens)
        topic_matches = sorted(topic_tokens & haystack_tokens)
        score = 3 * len(section_matches) + len(topic_matches)
        if kind in {"article", "product"}:
            title_tokens = _tokens(candidate.get("title", ""))
            score += 2 * len(section_tokens & title_tokens)
            score += len(topic_tokens & title_tokens)
        if kind == "article":
            keyword_tokens = _tokens(candidate.get("primary_keyword", ""))
            score += len(section_tokens & keyword_tokens)
        if kind == "evidence":
            concept_tokens = _tokens(" ".join(candidate.get("concepts", [])))
            score += 2 * len(section_tokens & concept_tokens)
            if candidate.get("required") and section_matches:
                score += 2
        if score <= 0:
            continue
        ranked.append({
            "candidate_id": candidate["candidate_id"],
            "score": score,
            "matched_terms": sorted(set(section_matches + topic_matches)),
            "section_matches": section_matches,
            "topic_matches": topic_matches,
            "section_match_count": len(section_matches),
            "title": candidate.get("title", ""),
            "url": candidate.get("url", ""),
            "product_id": candidate.get("product_id", ""),
            "required": bool(candidate.get("required")),
        })
    ranked.sort(key=lambda item: (-item["score"], item["candidate_id"]))
    return ranked


def _parse_number(value: Any) -> tuple[float, str] | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value), ""
    if not isinstance(value, str):
        return None
    match = re.search(r"(?i)(-?\d+(?:\.\d+)?)\s*([a-z%]+)?", value)
    if not match:
        return None
    return float(match.group(1)), (match.group(2) or "").casefold()


def _comparable_number(value: Any, expected_unit: str) -> float | None:
    parsed = _parse_number(value)
    if parsed is None:
        return None
    number, actual_unit = parsed
    expected = expected_unit.casefold()
    if not expected or not actual_unit or expected == actual_unit:
        return number
    actual_factor = _UNIT_FACTORS.get(actual_unit)
    expected_factor = _UNIT_FACTORS.get(expected)
    if actual_factor is None or expected_factor is None:
        return None
    if actual_factor[0] != expected_factor[0]:
        return None
    return number * actual_factor[1] / expected_factor[1]


def _constraint_failure(
    candidate: dict[str, Any],
    constraint: dict[str, Any],
) -> str:
    field = _field_key(constraint["field"])
    if field in candidate:
        actual = candidate[field]
    else:
        actual = candidate.get("attributes", {}).get("merged", {}).get(field)
    operator = constraint["operator"]
    expected = constraint.get("value")
    if operator == "exists":
        return "" if actual not in {None, ""} else "constraint_field_missing"
    if actual is None:
        return "constraint_field_missing"
    if operator in {"lte", "gte"}:
        number = _comparable_number(actual, constraint.get("unit", ""))
        if number is None:
            return "constraint_value_unusable"
        if operator == "lte" and number > float(expected):
            return "constraint_lte_failed"
        if operator == "gte" and number < float(expected):
            return "constraint_gte_failed"
        return ""
    actual_text = _clean_text(str(actual)).casefold()
    if operator == "equals":
        return "" if actual_text == _clean_text(str(expected)).casefold() else "constraint_equals_failed"
    if operator == "contains":
        return "" if _clean_text(str(expected)).casefold() in actual_text else "constraint_contains_failed"
    if operator == "one_of":
        allowed = {_clean_text(str(item)).casefold() for item in expected}
        return "" if actual_text in allowed else "constraint_one_of_failed"
    return "constraint_operator_unknown"


def _brief_catalog_conflicts(
    section: dict[str, Any],
    catalog_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    catalog_specs = set(catalog_profile.get("catalog_spec_tokens", []))
    conflicts: list[dict[str, Any]] = []
    for point in section.get("brief_points", []):
        if not _PRESCRIPTIVE_PATTERN.search(point):
            continue
        specs = _spec_tokens(point)
        missing = sorted(specs - catalog_specs)
        if missing:
            conflicts.append({
                "reason_code": "prescriptive_brief_catalog_mismatch",
                "brief_point": point,
                "missing_catalog_specs": missing,
            })
    return conflicts


def _resolved_gate(
    ranked: list[dict[str, Any]],
    *,
    limit: int,
    state: str,
    reason_code: str,
    empty_reason_code: str = "no_relevant_candidates",
    rejected: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    selected_ids = [item["candidate_id"] for item in ranked[:limit]]
    if not selected_ids:
        return {
            "opportunity_state": "none",
            "candidate_count": 0,
            "min_required": 0,
            "max_allowed": 0,
            "reason_code": empty_reason_code,
            "selected_ids": [],
            "rejected": list(rejected or []),
        }
    return {
        "opportunity_state": state,
        "candidate_count": len(selected_ids),
        "min_required": 1 if state == "required" else 0,
        "max_allowed": limit,
        "reason_code": reason_code,
        "selected_ids": selected_ids,
        "rejected": list(rejected or []),
    }


def _annotate_product_fit(
    ranked: list[dict[str, Any]],
    fit_level: str,
    fit_reason: str,
) -> list[dict[str, Any]]:
    return [
        {
            **item,
            "fit_level": fit_level,
            "fit_reason": fit_reason,
        }
        for item in ranked
    ]


def resolve_shadow_opportunities(
    section_contracts: dict[str, Any],
    link_contracts: dict[str, Any],
    candidate_registry: dict[str, Any],
) -> dict[str, Any]:
    sections = validate_section_contracts(section_contracts)
    links = validate_section_link_contracts(link_contracts, sections)
    registry = validate_candidate_registry(candidate_registry)
    link_by_id = {item["section_id"]: item for item in links["sections"]}
    resolved_sections: list[dict[str, Any]] = []
    manifest_sections: list[dict[str, Any]] = []
    opportunity_counts = {"required": 0, "recommended": 0, "none": 0}

    for section in sections["sections"]:
        article_ranked = _rank(
            registry["articles"]["candidates"],
            section,
            kind="article",
            topic=sections["topic"],
            ignored_section_tokens=set(
                registry["article_profile"].get("common_article_tokens", [])
            ),
        )
        article_ranked = [
            item for item in article_ranked if item["section_match_count"] >= 1
        ]
        evidence_ranked = _rank(
            registry["evidence"]["candidates"],
            section,
            kind="evidence",
            topic=sections["topic"],
        )
        constraint_rejections: list[dict[str, Any]] = []
        eligible_products: list[dict[str, Any]] = []
        for candidate in registry["products"]["candidates"]:
            failures = []
            if candidate.get("attribute_conflicts"):
                failures.append("product_attribute_conflict")
            failures.extend(
                failure
                for constraint in section.get("product_constraints", [])
                if (failure := _constraint_failure(candidate, constraint))
            )
            if failures:
                constraint_rejections.append({
                    "candidate_id": candidate["candidate_id"],
                    "product_id": candidate["product_id"],
                    "title": candidate["title"],
                    "reason_codes": sorted(set(failures)),
                    "attribute_conflicts": candidate.get("attribute_conflicts", []),
                })
            else:
                eligible_products.append(candidate)
        product_specific_ranked = _rank(
            eligible_products,
            section,
            kind="product",
            topic=sections["topic"],
            ignored_section_tokens=set(
                registry["catalog_profile"].get("common_product_tokens", [])
            ),
            text_field="eligibility_text",
        )
        constraints = section.get("product_constraints", [])
        strong_products = [
            item
            for item in product_specific_ranked
            if item["section_match_count"] >= 2
        ]
        contextual_products = [
            item
            for item in product_specific_ranked
            if item["section_match_count"] == 1
        ]
        fallback_products = [
            item
            for item in _rank(
                eligible_products,
                section,
                kind="product",
                topic=sections["topic"],
                text_field="eligibility_text",
            )
            if item["topic_matches"]
        ]
        if constraints:
            product_ranked = _annotate_product_fit(
                fallback_products,
                "approved_constraint",
                "matches_approved_structured_product_constraints",
            )
        elif strong_products:
            product_ranked = _annotate_product_fit(
                strong_products,
                "strong",
                "matches_multiple_section_specific_terms",
            )
            seen_ids = {item["candidate_id"] for item in product_ranked}
            product_ranked.extend(
                item
                for item in _annotate_product_fit(
                    contextual_products,
                    "contextual",
                    "matches_one_section_specific_term",
                )
                if item["candidate_id"] not in seen_ids
            )
        elif contextual_products:
            product_ranked = _annotate_product_fit(
                contextual_products,
                "contextual",
                "matches_one_section_specific_term",
            )
        else:
            product_ranked = _annotate_product_fit(
                fallback_products,
                "related_catalog",
                "related_to_article_topic_without_exact_use_case_match",
            )
        brief_conflicts = _brief_catalog_conflicts(section, registry["catalog_profile"])

        article_required = (
            bool(article_ranked)
            and article_ranked[0]["score"] >= 6
            and article_ranked[0]["section_match_count"] >= 2
            and section["reader_stage"] != "discover"
        )
        article_gate = _resolved_gate(
            article_ranked,
            limit=ARTICLE_CANDIDATE_LIMIT,
            state="required" if article_required else "recommended",
            reason_code=(
                "high_relevance_article_candidate"
                if article_required
                else "relevant_article_candidate"
            ),
            rejected=registry["articles"]["rejected"],
            empty_reason_code="no_relevant_article_candidates",
        )

        if not section["product_link_allowed"]:
            product_gate = link_by_id[section["section_id"]]["product_links"]
        else:
            top_fit = product_ranked[0].get("fit_level") if product_ranked else ""
            product_required = (
                bool(product_ranked)
                and top_fit
                in {
                    "strong",
                    "approved_constraint",
                    "contextual",
                    "related_catalog",
                }
                and section["reader_stage"] in {"select", "compare", "apply"}
            )
            product_reason = (
                "high_relevance_catalog_product"
                if product_required and top_fit in {"strong", "approved_constraint"}
                else "catalog_truth_overrides_brief"
                if product_required and brief_conflicts
                else "related_catalog_product"
                if product_required and top_fit == "related_catalog"
                else "contextual_catalog_product"
                if product_required and top_fit == "contextual"
                else "relevant_catalog_product"
                if product_ranked
                else "no_relevant_catalog_products"
            )
            product_gate = _resolved_gate(
                product_ranked,
                limit=PRODUCT_CANDIDATE_LIMIT,
                state="required" if product_required else "recommended",
                reason_code=product_reason,
                rejected=constraint_rejections,
                empty_reason_code="no_relevant_catalog_products",
            )

        evidence_required = bool(evidence_ranked) and (
            any(
                item["required"] and item["section_match_count"] >= 1
                for item in evidence_ranked
            )
            or (
                section["reader_stage"] == "verify"
                and evidence_ranked[0]["section_match_count"] >= 1
            )
        )
        evidence_gate = _resolved_gate(
            evidence_ranked,
            limit=EVIDENCE_CANDIDATE_LIMIT,
            state="required" if evidence_required else "recommended",
            reason_code=(
                "required_or_verification_evidence"
                if evidence_required
                else "relevant_evidence_candidate"
            ),
            rejected=registry["evidence"]["rejected"],
            empty_reason_code="no_relevant_evidence_candidates",
        )

        resolved = {
            "section_id": section["section_id"],
            "article_links": article_gate,
            "product_links": product_gate,
            "external_citations": evidence_gate,
        }
        resolved_sections.append(resolved)
        for gate in (article_gate, product_gate, evidence_gate):
            opportunity_counts[gate["opportunity_state"]] += 1
        manifest_sections.append({
            "section_id": section["section_id"],
            "heading": section["heading"],
            "reader_stage": section["reader_stage"],
            "article_candidates": article_ranked[:ARTICLE_CANDIDATE_LIMIT],
            "product_candidates": product_ranked[:PRODUCT_CANDIDATE_LIMIT],
            "product_rejections": constraint_rejections,
            "brief_catalog_conflicts": brief_conflicts,
            "evidence_candidates": evidence_ranked[:EVIDENCE_CANDIDATE_LIMIT],
        })

    resolved_links = {
        "version": CONTRACT_VERSION,
        "topic": sections["topic"],
        "content_language": sections["content_language"],
        "section_order": sections["section_order"],
        "sections": resolved_sections,
    }
    validate_section_link_contracts(resolved_links, sections)
    registry_json = json.dumps(registry, sort_keys=True, ensure_ascii=False).encode("utf-8")
    manifest = {
        "version": CONTRACT_VERSION,
        "topic": sections["topic"],
        "content_language": sections["content_language"],
        "registry_sha256": hashlib.sha256(registry_json).hexdigest(),
        "catalog_profile": registry["catalog_profile"],
        "catalog_data_issues": registry["catalog_data_issues"],
        "registry": registry,
        "sections": manifest_sections,
    }
    report = {
        "version": CONTRACT_VERSION,
        "topic": sections["topic"],
        "content_language": sections["content_language"],
        "section_count": len(resolved_sections),
        "opportunity_counts": opportunity_counts,
        "registry_counts": {
            kind: len(registry[kind]["candidates"])
            for kind in ("articles", "products", "evidence")
        },
        "rejection_counts": {
            kind: len(registry[kind]["rejected"])
            for kind in ("articles", "products", "evidence")
        },
        "brief_catalog_conflict_count": sum(
            len(item["brief_catalog_conflicts"]) for item in manifest_sections
        ),
        "catalog_profile": registry["catalog_profile"],
        "catalog_data_issue_count": len(registry["catalog_data_issues"]),
        "catalog_data_issues": registry["catalog_data_issues"],
        "formal_draft_modified": False,
        "ai_called": False,
    }
    return {
        "section_link_contracts": resolved_links,
        "context_manifest": manifest,
        "shadow_report": report,
    }


def shadow_paths(workspace: Path, slug: str) -> dict[str, Path]:
    base = contract_paths(workspace, slug)
    research = Path(workspace) / "research"
    return {
        "section_link_contracts": base["section_link_contracts"],
        "context_manifest": research / f"section-context-manifest-{slug}.json",
        "shadow_report": research / f"sectional-shadow-report-{slug}.json",
    }


def _json_bytes(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def persist_shadow_context(
    workspace: Path,
    slug: str,
    shadow_bundle: dict[str, Any],
    section_contracts: dict[str, Any],
) -> dict[str, str]:
    required_keys = {"section_link_contracts", "context_manifest", "shadow_report"}
    if not isinstance(shadow_bundle, dict) or set(shadow_bundle) != required_keys:
        raise ContractValidationError("shadow bundle keys are invalid")
    validate_section_link_contracts(shadow_bundle["section_link_contracts"], section_contracts)
    for name in ("context_manifest", "shadow_report"):
        data = shadow_bundle[name]
        if not isinstance(data, dict) or data.get("version") != CONTRACT_VERSION:
            raise ContractValidationError(f"{name} must be a version 1 object")
    paths = shadow_paths(workspace, slug)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    snapshots = {
        name: path.read_bytes() if path.exists() else None
        for name, path in paths.items()
    }
    temps: dict[str, Path] = {}
    try:
        for name, path in paths.items():
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            temp_path = Path(temp_name)
            temps[name] = temp_path
            with os.fdopen(fd, "wb") as handle:
                handle.write(_json_bytes(shadow_bundle[name]))
                handle.flush()
                os.fsync(handle.fileno())
        for name, path in paths.items():
            os.replace(temps[name], path)
        return {name: str(path) for name, path in paths.items()}
    except Exception:
        for name, path in paths.items():
            snapshot = snapshots[name]
            if snapshot is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(snapshot)
        raise
    finally:
        for temp in temps.values():
            temp.unlink(missing_ok=True)
