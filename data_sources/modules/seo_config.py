"""SEO workflow centralized configuration.

Single source of truth for thresholds and weights used across the PLAN,
RESEARCH and WRITE pipeline scripts. Importing this module is preferred over
hard-coding magic numbers in individual scripts.

All values here were extracted from the pre-refactor duplicates spread across
plan_scorer.py / research_scorer.py / content_scorer.py / write_collector.py /
write_pre_check.py / seo_common.py / cannibalization_checker.py.

Cannibalization note:
  The block threshold was lowered 0.65 -> 0.55 per SEO review (same-vertical
  terms inflate TF-IDF; the old 0.65 let near-duplicate articles through).
  This is an intentional SEO optimization decision, not a regression.
"""

# ── Cannibalization thresholds (TF-IDF cosine similarity) ──────────────
CANNIBAL_THRESHOLD_LOW = 0.45    # safe line — below this, not flagged
CANNIBAL_THRESHOLD_MID = 0.50    # warning line — needs differentiation
CANNIBAL_THRESHOLD_HIGH = 0.55   # block line (was 0.65; lowered per SEO review)

# ── Link density (1 link per N body words) ─────────────────────────────
LINK_RATIO_BLOG = 1000
LINK_RATIO_PRODUCT = 1300
LINK_RATIO_EXTERNAL = 800

# ── Content quality scoring ─────────────────────────────────────────────
PASS_SCORE = 70                    # composite-score pass line for content_scorer
CONTENT_WEIGHTS = {
    'humanity': 0.25,
    'specificity': 0.25,
    'structure_balance': 0.20,
    'seo': 0.25,
    'readability': 0.05,
}

# ── Page-tier minimum word counts ───────────────────────────────────────
TIER_MIN_WORDS = {
    'Pillar Page': 2500,
    'Cluster Content': 1200,
    'Product Roundup': 2000,
    # lower-case aliases kept for backward compatibility with callers that
    # pass the short tier key instead of the full label.
    'pillar': 2500,
    'cluster': 1200,
    'roundup': 2000,
}

# ── PLAN scorer weights (DISCOVER pipeline) ────────────────────────────
# Keys mirror the factor names used in plan_scorer's JSON output.
PLAN_WEIGHTS = {
    'demand': 0.30,        # is there real search demand?
    'cluster_gap': 0.25,   # is this a blue-ocean cluster?
    'pain_point': 0.25,    # do users actually hurt here?
    'value': 0.20,         # commercial / authority value
}
# Penalty multiplier applied when a candidate matches user-rejected feedback.
FEEDBACK_PENALTY = 0.20

# ── RESEARCH scorer weights (6-factor opportunity score) ──────────────
RESEARCH_WEIGHTS = {
    'demand': 0.20,
    'current_rank': 0.20,
    'competition': 0.20,
    'intent_match': 0.15,
    'content_gap': 0.15,
    'link_fit': 0.10,
}