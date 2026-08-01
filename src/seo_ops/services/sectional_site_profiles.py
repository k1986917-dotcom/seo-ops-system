"""Versioned site-specific product recommendation profiles.

The sectional ranking engine is shared by every site.  A profile may add
site-specific merchandising signals, candidate limits, and section-level
product exclusions without changing the generic catalog parser or scorer.
Unknown sites intentionally fall back to the conservative generic profile.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProductPreference:
    """One product-text signal used by a site use-case rule."""

    reason: str
    terms: tuple[str, ...]
    weight: int


@dataclass(frozen=True)
class ProductUseCaseRule:
    """Activate product preferences when the article context matches a use case."""

    name: str
    triggers: tuple[str, ...]
    minimum_trigger_matches: int
    preferences: tuple[ProductPreference, ...]


@dataclass(frozen=True)
class SectionalSiteProfile:
    """Configuration consumed by the shared sectional recommendation engine."""

    site_slug: str
    version: int
    product_candidate_limit: int
    product_link_limit_per_section: int
    unique_product_per_article: bool
    required_fit_levels: frozenset[str]
    generic_product_terms: frozenset[str]
    prohibited_product_section_patterns: tuple[str, ...]
    use_case_rules: tuple[ProductUseCaseRule, ...]
    high_power_policy: str
    variant_label_pattern: str
    source: str


GENERIC_PROFILE = SectionalSiteProfile(
    site_slug="default",
    version=1,
    product_candidate_limit=2,
    product_link_limit_per_section=2,
    unique_product_per_article=False,
    required_fit_levels=frozenset(
        {"strong", "approved_constraint", "contextual", "related_catalog"}
    ),
    generic_product_terms=frozenset({"product", "products", "tool", "tools"}),
    prohibited_product_section_patterns=(),
    use_case_rules=(),
    high_power_policy="not_applicable",
    variant_label_pattern="",
    source="builtin:default",
)

_PROFILE_DIR = Path(__file__).resolve().parents[1] / "site_profiles"
_SITE_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_FIT_LEVELS = frozenset({"strong", "approved_constraint", "contextual", "related_catalog"})


class SectionalSiteProfileError(ValueError):
    """Raised when an installed site profile is malformed."""


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SectionalSiteProfileError(f"{field} must be a non-empty string")
    return " ".join(value.split())


def _string_tuple(value: Any, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise SectionalSiteProfileError(f"{field} must be a non-empty string list")
    result = tuple(_required_text(item, f"{field}[]") for item in value)
    if len(result) != len(set(result)):
        raise SectionalSiteProfileError(f"{field} must not contain duplicates")
    return result


def _positive_int(value: Any, field: str, *, maximum: int = 100) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise SectionalSiteProfileError(f"{field} must be an integer from 1 to {maximum}")
    return value


def _profile_from_payload(payload: Any, *, source: str) -> SectionalSiteProfile:
    if not isinstance(payload, dict):
        raise SectionalSiteProfileError("site profile must be a JSON object")
    site_slug = _required_text(payload.get("site_slug"), "site_slug").casefold()
    if not _SITE_SLUG.fullmatch(site_slug):
        raise SectionalSiteProfileError("site_slug contains unsupported characters")
    version = _positive_int(payload.get("version"), "version", maximum=999)
    product_candidate_limit = _positive_int(
        payload.get("product_candidate_limit"),
        "product_candidate_limit",
        maximum=20,
    )
    product_link_limit = _positive_int(
        payload.get("product_link_limit_per_section"),
        "product_link_limit_per_section",
        maximum=10,
    )
    if product_link_limit > product_candidate_limit:
        raise SectionalSiteProfileError(
            "product_link_limit_per_section cannot exceed product_candidate_limit"
        )
    unique_product_per_article = payload.get("unique_product_per_article")
    if not isinstance(unique_product_per_article, bool):
        raise SectionalSiteProfileError("unique_product_per_article must be bool")
    required_fit_levels = frozenset(
        _string_tuple(payload.get("required_fit_levels"), "required_fit_levels")
    )
    if not required_fit_levels <= _FIT_LEVELS:
        raise SectionalSiteProfileError("required_fit_levels contains an unknown fit level")
    generic_product_terms = frozenset(
        term.casefold()
        for term in _string_tuple(
            payload.get("generic_product_terms"),
            "generic_product_terms",
            allow_empty=True,
        )
    )
    prohibited_patterns = _string_tuple(
        payload.get("prohibited_product_section_patterns"),
        "prohibited_product_section_patterns",
        allow_empty=True,
    )
    for pattern in prohibited_patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SectionalSiteProfileError(
                f"prohibited_product_section_patterns contains invalid regex: {pattern}"
            ) from exc
    variant_label_pattern = str(payload.get("variant_label_pattern") or "")
    if variant_label_pattern:
        try:
            re.compile(variant_label_pattern)
        except re.error as exc:
            raise SectionalSiteProfileError("variant_label_pattern is invalid") from exc

    raw_rules = payload.get("use_case_rules")
    if not isinstance(raw_rules, list):
        raise SectionalSiteProfileError("use_case_rules must be a list")
    rules: list[ProductUseCaseRule] = []
    rule_names: set[str] = set()
    for index, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, dict):
            raise SectionalSiteProfileError(f"use_case_rules[{index}] must be an object")
        name = _required_text(raw_rule.get("name"), f"use_case_rules[{index}].name")
        if name in rule_names:
            raise SectionalSiteProfileError(f"duplicate use-case rule: {name}")
        rule_names.add(name)
        triggers = _string_tuple(
            raw_rule.get("triggers"),
            f"use_case_rules[{index}].triggers",
        )
        minimum_trigger_matches = _positive_int(
            raw_rule.get("minimum_trigger_matches"),
            f"use_case_rules[{index}].minimum_trigger_matches",
            maximum=len(triggers),
        )
        raw_preferences = raw_rule.get("preferences")
        if not isinstance(raw_preferences, list) or not raw_preferences:
            raise SectionalSiteProfileError(
                f"use_case_rules[{index}].preferences must be a non-empty list"
            )
        preferences: list[ProductPreference] = []
        preference_reasons: set[str] = set()
        for preference_index, raw_preference in enumerate(raw_preferences):
            if not isinstance(raw_preference, dict):
                raise SectionalSiteProfileError(
                    f"use_case_rules[{index}].preferences[{preference_index}] must be an object"
                )
            reason = _required_text(
                raw_preference.get("reason"),
                f"use_case_rules[{index}].preferences[{preference_index}].reason",
            )
            if reason in preference_reasons:
                raise SectionalSiteProfileError(f"duplicate preference reason in {name}: {reason}")
            preference_reasons.add(reason)
            terms = _string_tuple(
                raw_preference.get("terms"),
                f"use_case_rules[{index}].preferences[{preference_index}].terms",
            )
            weight = _positive_int(
                raw_preference.get("weight"),
                f"use_case_rules[{index}].preferences[{preference_index}].weight",
                maximum=1000,
            )
            preferences.append(ProductPreference(reason, terms, weight))
        rules.append(
            ProductUseCaseRule(
                name=name,
                triggers=triggers,
                minimum_trigger_matches=minimum_trigger_matches,
                preferences=tuple(preferences),
            )
        )

    return SectionalSiteProfile(
        site_slug=site_slug,
        version=version,
        product_candidate_limit=product_candidate_limit,
        product_link_limit_per_section=product_link_limit,
        unique_product_per_article=unique_product_per_article,
        required_fit_levels=required_fit_levels,
        generic_product_terms=generic_product_terms,
        prohibited_product_section_patterns=prohibited_patterns,
        use_case_rules=tuple(rules),
        high_power_policy=_required_text(
            payload.get("high_power_policy"),
            "high_power_policy",
        ),
        variant_label_pattern=variant_label_pattern,
        source=source,
    )


@lru_cache(maxsize=32)
def get_sectional_site_profile(site_slug: str | None) -> SectionalSiteProfile:
    """Load one packaged site profile or return the generic fallback."""

    normalized = str(site_slug or "").strip().casefold()
    if not normalized:
        return GENERIC_PROFILE
    if not _SITE_SLUG.fullmatch(normalized):
        raise SectionalSiteProfileError("site slug contains unsupported characters")
    path = _PROFILE_DIR / f"{normalized}.json"
    if not path.is_file():
        return GENERIC_PROFILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SectionalSiteProfileError(
            f"cannot load sectional site profile: {normalized}"
        ) from exc
    profile = _profile_from_payload(
        payload,
        source=f"package:site_profiles/{path.name}",
    )
    if profile.site_slug != normalized:
        raise SectionalSiteProfileError(
            f"profile site_slug mismatch: expected {normalized}, got {profile.site_slug}"
        )
    return profile


def _normalize(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _phrase_present(text: str, phrase: str) -> bool:
    normalized = _normalize(phrase)
    if not normalized:
        return False
    if " " in normalized or "-" in normalized:
        return normalized in text
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text))


def product_section_block_reason(
    profile: SectionalSiteProfile,
    *,
    heading: str,
    reader_question: str = "",
    section_goal: str = "",
) -> str:
    """Return a deterministic reason when this site forbids product links."""

    text = _normalize(" ".join([heading, reader_question, section_goal]))
    for pattern in profile.prohibited_product_section_patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return "site_profile_prohibits_product_link"
    return ""


def score_site_product_preferences(
    profile: SectionalSiteProfile,
    *,
    article_context: str,
    product_text: str,
) -> dict[str, Any]:
    """Return additive site-specific score and auditable matched reasons."""

    context = _normalize(article_context)
    product = _normalize(product_text)
    score = 0
    active_rules: list[str] = []
    matched_preferences: list[str] = []
    for rule in profile.use_case_rules:
        trigger_count = sum(_phrase_present(context, trigger) for trigger in rule.triggers)
        if trigger_count < rule.minimum_trigger_matches:
            continue
        active_rules.append(rule.name)
        for preference in rule.preferences:
            if any(_phrase_present(product, term) for term in preference.terms):
                score += preference.weight
                matched_preferences.append(f"{rule.name}:{preference.reason}")
    return {
        "score": score,
        "active_rules": active_rules,
        "matched_preferences": matched_preferences,
    }


def select_site_product_variant(
    profile: SectionalSiteProfile,
    *,
    article_context: str,
    product_text: str,
) -> dict[str, Any] | None:
    """Select the best named variant without changing the catalog candidate ID."""

    if not profile.variant_label_pattern:
        return None
    matches = list(
        re.finditer(profile.variant_label_pattern, str(product_text or ""), flags=re.IGNORECASE)
    )
    labels = {match.group(0).casefold() for match in matches}
    if len(labels) < 2:
        return None
    choices: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(product_text)
        segment = " ".join(str(product_text[match.start() : end]).split())
        preference = score_site_product_preferences(
            profile,
            article_context=article_context,
            product_text=segment,
        )
        choices.append(
            {
                "label": match.group(0).upper(),
                "text": segment[:500],
                "site_preference_score": int(preference["score"]),
                "matched_site_preferences": list(preference["matched_preferences"]),
            }
        )
    best = sorted(
        choices,
        key=lambda item: (-item["site_preference_score"], item["label"]),
    )[0]
    return best if best["site_preference_score"] > 0 else None


def site_profile_manifest(profile: SectionalSiteProfile) -> dict[str, Any]:
    """Return the compact, serializable profile metadata stored with a run."""

    return {
        "site_slug": profile.site_slug,
        "version": profile.version,
        "product_candidate_limit": profile.product_candidate_limit,
        "product_link_limit_per_section": profile.product_link_limit_per_section,
        "unique_product_per_article": profile.unique_product_per_article,
        "required_fit_levels": sorted(profile.required_fit_levels),
        "high_power_policy": profile.high_power_policy,
        "variant_label_pattern": profile.variant_label_pattern,
        "use_case_rules": [rule.name for rule in profile.use_case_rules],
        "source": profile.source,
    }
