---
Title: "Example Topic for Testing the Legacy Pipeline: A Practical Integration Guide"
Slug: example-topic-for-testing-the-legacy-pipeline
Author: Examplesite
Summary: Synthetic placeholder article that exercises the SEO Ops Legacy workflow's draft schema. Use as a fixture when testing W0..W3; do not index or link from production pages.
Tags: test fixture, pipeline, CI integration, schema validation, examplesite
SEO Title: "Example Topic for Testing the Legacy Pipeline: A Guide"
SEO Description: A synthetic article used to test the SEO Ops Legacy workflow's draft schema. Not real content; do not index.
SEO Keywords: example topic, legacy pipeline, test fixture, CI integration
---

# Example Topic for Testing the Legacy Pipeline: A Practical Integration Guide

Most teams who adopt a test pipeline for SEO content operations assume the hard part is writing the schema. That's exactly why most teams end up with fixtures that rot within a quarter. The right framing isn't "schema" — it's **contract test**: a fixture encodes the *shape* the production system promises to deliver, and the pipeline verifies that shape on every change.

This guide covers the integration patterns that turn the example pipeline into a CI-runnable contract test, the failure modes that surface when production data evolves, and the migration path from manual review to automated gating.

> **Key Takeaways**
> - Treat the example pipeline as a contract test, not as content review
> - Synthetic fixtures preserve schema; real production data still drives content decisions
> - CI integration has 4 patterns: pre-commit, PR check, nightly, release gate
> - Failure modes (schema drift, slug collision, frontmatter parse, link validation) are predictable
> - Migration to automated gating takes 3 phases and ~6 weeks of engineering

### Quick Specs: Example Pipeline Integration

- **Wavelength**: N/A (test infrastructure)
- **Output Power**: N/A (no LLM cost; pipeline runs on synthetic data)
- **Battery**: <30 seconds end-to-end on a CI runner with 4 CPU cores
- **Build**: schema validator + frontmatter linter + link checker + slug uniqueness check
- **Price**: $0 (open source)
- **Key Differentiator**: Synthetic fixtures are deterministic across runs, so CI flakes become impossible
_These specs are placeholder values for fixture purposes only._

## Why a Test Pipeline Matters for SEO Content Operations

The traditional review process for SEO content is slow and inconsistent. One editor accepts a 1500-word draft, another rejects it for being "too short"; one product manager wants 5 internal links, another wants 3. Without a contract, the review process becomes a negotiation, and the result depends on who has time that day.

The example pipeline flips this: it encodes the contract — "every article has frontmatter with Title/Slug/Author/Tags/SEO Title/SEO Description/SEO Keywords, plus 4-7 H2 sections, plus a FAQ block" — and runs that contract test on every change.

Compared to legacy snapshot tests, which store the entire current output and fail on any diff, the example pipeline stores the *schema* and accepts content variation as long as the shape is correct. This is the difference between a brittle regression test and a useful contract test.

## CI/CD Integration Patterns

There are four integration patterns, each with different cost/benefit tradeoffs.

### Pattern 1: Pre-commit hook

A pre-commit hook runs the example pipeline against staged fixtures before the commit is created. Failure blocks the commit.

- **Cost**: <1 second per commit; no infrastructure beyond git
- **Benefit**: catches schema mistakes before they reach the remote
- **Limitation**: developers can bypass with `--no-verify`; not a hard gate

### Pattern 2: PR check

A GitHub Actions / GitLab CI job runs the example pipeline on every pull request. Failure blocks merge.

- **Cost**: 30-60 seconds per PR; one CI runner
- **Benefit**: hard gate; cannot merge broken fixtures
- **Limitation**: only catches what the PR changed; misses accumulated drift

### Pattern 3: Nightly run

A scheduled nightly job runs the example pipeline against the *full* fixture set. Failure creates an issue.

- **Cost**: 5-15 minutes per night
- **Benefit**: catches accumulated drift; surfaces problems that individual PRs missed
- **Limitation**: problems surface hours after introduction; not a real-time gate

### Pattern 4: Release gate

A release-blocking job runs the example pipeline against the production-schema snapshot. Failure blocks the production deploy.

- **Cost**: 1-3 minutes per release
- **Benefit**: guarantees production schema matches the test fixture
- **Limitation**: only useful at release time; doesn't catch day-to-day drift

A typical team uses 2 or 3 of these in combination; the right answer depends on team size and deploy frequency.

## Failure Modes and Recovery

The example pipeline surfaces a small set of failure modes, each with a known recovery path.

### Failure mode 1: Schema drift

`legacy_sync` adds a new column to the published-index.json schema. The example pipeline's parser fails on the new field.

**Recovery**: update the parser in `seo_ops.services.legacy_sync`; regenerate fixtures if the schema is now backwards-incompatible.

### Failure mode 2: Slug collision

Two articles have the same slug, breaking uniqueness in the index.

**Recovery**: investigate which slug is canonical; rename the duplicate and add a redirect.

### Failure mode 3: Frontmatter parse failure

A new article has malformed YAML in its frontmatter (e.g., a string with an unescaped colon).

**Recovery**: the frontmatter linter reports the exact line; fix and re-run.

### Failure mode 4: External link 4xx

An article links to an external URL that has since returned 404.

**Recovery**: the link checker identifies the dead URL; either update to a current source or remove the link.

## Migration from Manual Workflows

Migrating from manual review to automated gating takes three phases.

**Phase 1 (1-2 weeks)**: shadow run. Run the example pipeline against real production data; do not enforce. Build a baseline of how often the pipeline fails and what kinds of failures it catches.

**Phase 2 (2-3 weeks)**: opt-in enforcement. Developers can see pipeline failures in PR checks but can still merge. Address the easy failures; document the hard ones.

**Phase 3 (1-2 weeks)**: required gate. Pipeline failure blocks merge. Most teams will hit one or two false-positive cases during this phase; address them with allow-lists, not by relaxing the gate.

## Production Deployment Checklist

- [ ] Pre-commit hook installed in every developer's local clone
- [ ] PR check workflow configured in GitHub Actions / GitLab CI
- [ ] Nightly run scheduled with issue auto-creation on failure
- [ ] Release gate workflow runs against the production schema snapshot
- [ ] Fixture regeneration script is idempotent
- [ ] Synthetic fixtures do not contain user-quoted text from real sources
- [ ] Sensitive-info scanner runs in CI and blocks on findings
- [ ] Documentation references the synthetic fixtures as the schema reference
- [ ] On-call rotation knows how to silence a flaky test (and how to file a fix)
- [ ] Quarterly review removes obsolete fixtures

## Conclusion

The example pipeline is a contract test, not a content review. Real fixtures come from real production data; synthetic fixtures let you iterate on the schema without depending on operator-pasted data. The four CI integration patterns (pre-commit, PR check, nightly, release gate) cover most team sizes; pick 2-3 of them and iterate.

Next step: copy `tests/fixtures/legacy_pipeline/` into a fresh data directory and click through R0..W3 in the UI to verify the round trip end-to-end.

## FAQ

### Q: Can synthetic fixtures replace real data?

A: No. Synthetic fixtures preserve schema shape, not values. You still need real production data for content decisions.

### Q: How often should I run the pipeline?

A: Pre-commit on every commit, PR check on every PR, nightly on the full fixture set, release gate on every production deploy.

### Q: What if my schema changes?

A: Update the parser first; the schema change is intentional. Then regenerate fixtures if the change is backwards-incompatible.

### Q: Can I bypass the gate for an emergency?

A: Yes, but the bypass should be logged and reviewed. A bypass that becomes routine indicates the gate is wrong, not that it should be relaxed.

### Q: Does this work without internet?

A: The fixture validation is fully offline. Real-data validation (e.g., GSC sync, link checker) needs internet.

### Q: What about real-data tests?

A: Add a separate "real" fixture set that runs against a copy of production data (sanitized). The contract test stays synthetic; the smoke test is real.

---

> This is a synthetic fixture. See `tests/fixtures/legacy_pipeline/README.md`.