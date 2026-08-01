"""Versioned site-specific product recommendation profiles.

The sectional ranking engine is shared by every site.  A profile may add
site-specific merchandising signals, candidate limits, and section-level
product exclusions without changing the generic catalog parser or scorer.
Unknown sites intentionally fall back to the conservative generic profile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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
)


LASERPOINTERHUB_PROFILE = SectionalSiteProfile(
    site_slug="laserpointerhub",
    version=1,
    product_candidate_limit=5,
    product_link_limit_per_section=1,
    unique_product_per_article=True,
    required_fit_levels=frozenset({"strong", "approved_constraint", "contextual"}),
    generic_product_terms=frozenset(
        {
            "laser",
            "lasers",
            "pointer",
            "pointers",
            "product",
            "products",
            "tool",
            "tools",
        }
    ),
    prohibited_product_section_patterns=(
        r"\b(?:safety|compliance|legal|regulation|regulatory|hazard|danger|injury|warning)\b",
        r"\bclass\s*(?:3r|3b|4)\b",
    ),
    use_case_rules=(
        ProductUseCaseRule(
            name="long_distance_visible_pointing",
            triggers=(
                "above",
                "bright",
                "ceiling",
                "distance",
                "long range",
                "overhead",
                "pointing",
                "visibility",
            ),
            minimum_trigger_matches=2,
            preferences=(
                ProductPreference("green_wavelength", ("520nm", "532nm"), 36),
                ProductPreference("green_color", ("green", "emerald"), 24),
                ProductPreference(
                    "visible_beam",
                    (
                        "highly visible",
                        "maximum visibility",
                        "perceived visibility",
                        "visible beam",
                        "visibility",
                    ),
                    16,
                ),
                ProductPreference(
                    "single_beam",
                    ("single beam", "single-beam", "pure focus", "no scatter"),
                    18,
                ),
                ProductPreference(
                    "focus_control",
                    ("adjustable focus", "adjustable focal", "focusing", "focus mechanism"),
                    12,
                ),
                ProductPreference(
                    "distance_feature",
                    ("long range", "long-range", "effective range", "at distance"),
                    14,
                ),
            ),
        ),
        ProductUseCaseRule(
            name="portable_field_use",
            triggers=("carry", "compact", "handheld", "pocket", "portable"),
            minimum_trigger_matches=1,
            preferences=(
                ProductPreference("compact_body", ("compact", "pocket", "pocket-sized"), 12),
                ProductPreference(
                    "rechargeable_power",
                    ("rechargeable", "usb charging", "usb-c charging", "built-in battery"),
                    8,
                ),
            ),
        ),
        ProductUseCaseRule(
            name="precision_pointing",
            triggers=("alignment", "focus", "precision", "targeting"),
            minimum_trigger_matches=1,
            preferences=(
                ProductPreference(
                    "focus_control",
                    ("adjustable focus", "adjustable focal", "focusing", "focus mechanism"),
                    18,
                ),
                ProductPreference(
                    "single_beam",
                    ("single beam", "single-beam", "pure focus", "no scatter"),
                    14,
                ),
            ),
        ),
        ProductUseCaseRule(
            name="burning_or_energy_density",
            triggers=("burn", "burning", "engrave", "ignite", "lighting matches"),
            minimum_trigger_matches=1,
            preferences=(
                ProductPreference(
                    "high_output_for_energy_use",
                    ("high power", "high-power", "energy density", "concentrated output"),
                    22,
                ),
                ProductPreference(
                    "focus_control",
                    ("adjustable focus", "adjustable focal", "focusing", "focus mechanism"),
                    16,
                ),
            ),
        ),
    ),
    high_power_policy="neutral_for_ranking_and_never_an_exclusion",
    variant_label_pattern=r"\b(?:B|G)\d{3}(?:\.\d+)?[A-Z]\b",
)


_PROFILES = {LASERPOINTERHUB_PROFILE.site_slug: LASERPOINTERHUB_PROFILE}


def get_sectional_site_profile(site_slug: str | None) -> SectionalSiteProfile:
    """Return a versioned site profile or the generic fallback."""

    normalized = str(site_slug or "").strip().casefold()
    return _PROFILES.get(normalized, GENERIC_PROFILE)


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
    }
