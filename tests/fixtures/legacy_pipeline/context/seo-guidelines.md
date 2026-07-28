# Examplesite SEO Guidelines

> SYNTHETIC TEST FIXTURE — see `tests/fixtures/legacy_pipeline/README.md`

## Content length

- **Target 1500-3000 words** for Cluster articles; 2500-4000 for Pillar pages
- Length follows information density, not the other way around
- Don't pad with restated introductions or generic "in conclusion" paragraphs

## Core SEO principles

### 1. E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness)

- **Experience**: cite real test data, community-reported observations (forums), and authoritative reports
- **Expertise**: show depth in the topic's underlying physics, standards, or methodology
- **Authoritativeness**: every core claim cites at least one external authoritative source (.gov, .edu, industry standard)
- **Trustworthiness**: don't inflate specifications; disclose test methods; numbers should be independently verifiable

### 2. Information gain

- Every article must include content the top 5 SERP competitors miss
- Sources: community-measured data, unique comparison tests, industry exposé, deep principle explanation
- Don't paraphrase competitors — provide a dimension they don't have

### 3. Frontmatter (set by W0; finalized by W2)

```yaml
---
Title: [H1, 50-60 chars, includes primary keyword]
Slug: [URL slug, lowercase, hyphenated]
Author: [Brand Name]
Summary: [2-3 sentence summary, 150-200 chars]
Tags: [3-5 comma-separated, English-only]
SEO Title: [50-60 chars]
SEO Description: [150-160 chars]
SEO Keywords: [comma-separated, primary first]
---
```

The link fields (`内链`, `外链`, `字数`) are written by `write_collector.py
post-process --apply`, not by the AI. AI leaves them empty.

### 4. Required structural elements (verified by W1b)

- [ ] H1 with primary keyword (matches `Title` frontmatter)
- [ ] Primary keyword in first 100 words
- [ ] Primary keyword in ≥2 H2 sections
- [ ] SEO Title 50-60 chars
- [ ] SEO Description 150-160 chars
- [ ] **Key Takeaways** block (3-5 bullets, specific conclusions not TOC)
- [ ] **Quick Specs** block (commercial articles only)
- [ ] 4-7 H2 sections, logical progression
- [ ] FAQ with 3-4 Q&A pairs
- [ ] FAQPage JSON-LD schema
- [ ] Each H2 paragraph 2-4 sentences, self-contained

### 5. Linking rules (verified by W2)

- **Blog internal links**: ~1 per 1000 words, min 2, max 8
- **Product internal links**: ~1 per 1300 words, min 1, max 3
- **External links**: ~1 per 800 words, min 2, max 8
- Every external link must trace back to a source cited in the material pack (E section)
- No fabricated URLs — verified by `write_collector.py`