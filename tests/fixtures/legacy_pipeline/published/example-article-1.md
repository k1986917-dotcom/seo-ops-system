---
Title: "Example Article One: A Complete Walkthrough"
Slug: example-article-1
Summary: A synthetic placeholder article used to demonstrate the published-article schema that the Legacy workflow reads during cannibalization checks and backlink generation.
Tags: example cluster one, walkthrough, tutorial
SEO Title: "Example Article One: Complete Walkthrough for the Test Pipeline"
SEO Description: Placeholder article used to demonstrate SEO Ops canonical frontmatter and body schema. Not real content; do not index.
SEO Keywords: example article one, walkthrough, test fixture
---

## Author & Review Information

- **Author:** Examplesite Test Team
- **Content Type:** Synthetic placeholder
- **Reviewed Against:** Internal fixture schema v1
- **Last Updated:** January 2026

---

## Introduction

This article is a placeholder used by the SEO Ops test pipeline. It
exercises the legacy_sync `_gen_published_articles` function and provides
a canonical URL and body for cannibalization checks and backlink generation.

The intent is to verify that the Legacy workflow can:

1. Read `published/published-index.json` and pick up this article
2. Match its slug and URL when generating backlinks for a new article
3. Compute a TF-IDF similarity score against a candidate draft
4. Render it in the "internal-links-map" candidate list

> ⚠️ **No real content.** This is a synthetic fixture; do not index, do not
> link to from production pages.

---

## Section 1: Example section heading

Placeholder body. Replace with real content when using this fixture in production.

### Subsection: example subsection

More placeholder body. The Legacy workflow's cannibalization check computes
TF-IDF similarity over this section's tokens.

| Column A | Column B |
|---|---|
| Example value 1 | Example value 2 |
| Example value 3 | Example value 4 |

---

## Section 2: Another example section

Another placeholder section. Real articles in this slot would include
inbound links from the new article and outbound links to product pages.

- Bullet point 1
- Bullet point 2
- Bullet point 3

---

## FAQ

### Q: Is this article real?

A: No. It is a SYNTHETIC test fixture under `tests/fixtures/legacy_pipeline/`.

### Q: Can I link to it from production pages?

A: No. Production links must not point at placeholder content.

### Q: What schema does this exercise?

A: SEO Ops canonical frontmatter (`Title`, `Slug`, `Author`, `Summary`, `Tags`, `SEO Title`, `SEO Description`, `SEO Keywords`) and a 2-section body with a table and a FAQ.