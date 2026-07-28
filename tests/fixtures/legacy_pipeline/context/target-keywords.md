# Examplesite Target Keywords

> SYNTHETIC TEST FIXTURE — see `tests/fixtures/legacy_pipeline/README.md`
> v1 — seed keywords only, no GSC data (the synthetic site has no GSC connection)

## Topic Cluster 1: example primary cluster (synthetic)

**Primary:** example primary cluster keyword
**Secondary:** secondary cluster keyword, related cluster term, support cluster term, related support, related context
**GSC verified:** *none* (synthetic site has no GSC connection)
**对应产品:** EXAMPLE-001, EXAMPLE-002
**已有文章数量:** 2篇 | **文章编号:** #1, #2
**Must-mention Entities / LSI:** example entity A, example entity B, synthetic standard XYZ, fake regulation reference, placeholder acronym QRS

## Topic Cluster 2: example secondary cluster (synthetic)

**Primary:** example secondary cluster keyword
**Secondary:** secondary keyword, support keyword, related term, variation term
**GSC verified:** *none*
**对应产品:** EXAMPLE-003
**已有文章数量:** 1篇 | **文章编号:** #3
**Must-mention Entities / LSI:** example entity C, example entity D, related standard, placeholder metric ABC

## Topic Cluster 3: example commercial cluster (synthetic)

**Primary:** example best-of commercial keyword
**Secondary:** example under $X, example review, example vs comparison
**GSC verified:** *none*
**对应产品:** 全品类
**已有文章数量:** 1篇 | **文章编号:** #4
**Must-mention Entities / LSI:** example checklist, example comparison axis 1, example comparison axis 2

---

## Real-data reference

The real `target-keywords.md` (laserpointerhub) is 5-6 topic clusters, each with:

- 5-15 secondaries
- 2-15 GSC-verified rows with impressions/CTR/position
- SKU mappings
- 1-2 LSI entity lists

For real-test fixtures, copy from `data/legacy_workflow/laserpointerhub/context/target-keywords.md`
(not in this repo, gitignored).