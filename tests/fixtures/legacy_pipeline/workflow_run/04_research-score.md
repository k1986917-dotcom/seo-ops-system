<!--
SYNTHETIC TEST FIXTURE — see tests/fixtures/legacy_pipeline/README.md
Deterministic opportunity score. Format produced by data_sources/modules/research_scorer.py.
-->
# Research Score — example-topic-for-testing-the-legacy-pipeline
> Date: 2026-01-15
> Site: examplesite
> Topic: Example Topic for Testing the Legacy Pipeline

## Composite score: 0.68

| Factor | Weight | Raw | Normalized | Contribution |
|---|---:|---:|---:|---:|
| demand_proxy | 0.30 | synthetic E2 keywords only (no GSC) | 0.40 | 0.120 |
| current_rank | 0.20 | new content, no current rank | 0.50 | 0.100 |
| competition | 0.20 | 4 generic SERP results, low quality | 0.85 | 0.170 |
| intent_match | 0.15 | SERP intent matches informational | 0.80 | 0.120 |
| content_gap | 0.15 | 3 gaps identified | 0.85 | 0.128 |
| link_fit | 0.10 | 2 product pages, 4 blog pages | 0.65 | 0.065 |

**Total: 0.703** → rounded to **0.68**

## Notes

- `demand_proxy` is below 0.5 because the synthetic site has no GSC data; only the operator-curated E2 list contributes.
- `competition` is high (0.85) because the SERP only has 4 placeholder results — easy to outrank but not a meaningful signal.
- `content_gap` is the strongest factor; the new article can introduce CI/CD integration patterns that no current SERP result covers.

## Tier recommendation

- `tier`: Cluster Content (1500-3000 words)
- `intent`: 混合型 (informational + commercial)
- `commercial_intent_score`: 0.45 (commercial keywords present but secondary)

## Cannibalization risk

- `cannibal_risk`: low
- `closest_match`: Example Article Two: A Practical Checklist (slug similarity 0.32)
- TF-IDF overlap (estimated): 0.18 (well below the 0.55 block threshold)