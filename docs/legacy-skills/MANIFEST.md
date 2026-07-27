# Legacy Skills Reference Snapshot — MANIFEST

Snapshot taken on: 2026-07-27
Snapshot purpose: Provide immutable reference copies of the original (legacy) SEO workflow skill definitions and the frozen scripts they invoke, so that downstream code review can verify whether the current system (`src/seo_ops/...`) conforms to the original business rules.

This snapshot is **read-only reference material**. Do not edit, refactor, or "modernize" these files in place. If business rules need to change, create new ADR documents and update the live system; keep this snapshot as the historical baseline.

---

## Source location

All files were copied byte-for-byte from:

```
/home/laoma/seo-workflow/
```

Original top-level layout:

```
/home/laoma/seo-workflow/
├── .opencode/
│   └── skills/
│       ├── research/SKILL.md
│       └── write/SKILL.md
└── data_sources/
    └── modules/
        ├── cannibalization_checker.py
        ├── content_scorer.py
        ├── research_collector.py
        ├── research_scorer.py
        ├── write_collector.py
        └── write_pre_check.py
```

---

## File inventory

### Primary skill documents (originals)

| Original absolute path | Repository path | Type | SHA-256 | Size (bytes) |
|---|---|---|---|---|
| `/home/laoma/seo-workflow/.opencode/skills/research/SKILL.md` | `docs/legacy-skills/research/SKILL.md` | original skill doc | `a2e9416be82230101c3b677df568f58dcdb0f4fadd4dcb138308df94d978caa7` | 12946 |
| `/home/laoma/seo-workflow/.opencode/skills/write/SKILL.md` | `docs/legacy-skills/write/SKILL.md` | original skill doc | `bbaccea1a833a396b159aae6aabdad91362aabc3a972baf43090c88c4ca85337` | 14512 |

### Referenced frozen scripts (directly invoked by skill docs)

| Original absolute path | Repository path | Type | SHA-256 | Size (bytes) |
|---|---|---|---|---|
| `/home/laoma/seo-workflow/data_sources/modules/research_collector.py` | `docs/legacy-skills/data_sources/modules/research_collector.py` | referenced frozen script | `aafa91c43661f4ca9ccdfba47a4d07379ce64de774ccbd81ca0b32039493cb7d` | 70055 |
| `/home/laoma/seo-workflow/data_sources/modules/research_scorer.py` | `docs/legacy-skills/data_sources/modules/research_scorer.py` | referenced frozen script | `561384a2e4895176a330810cc1d0d19d4302f48acb706870e4915c4fefee9de5` | 10880 |
| `/home/laoma/seo-workflow/data_sources/modules/cannibalization_checker.py` | `docs/legacy-skills/data_sources/modules/cannibalization_checker.py` | referenced frozen script | `b2fde8753f706b8ee2e731f1851cd4ce3cd4b294cd8e8ed348f2a15d2be5a1cb` | 10443 |
| `/home/laoma/seo-workflow/data_sources/modules/write_collector.py` | `docs/legacy-skills/data_sources/modules/write_collector.py` | referenced frozen script | `c11aac32721f773026ff7d7ee68131dcf99958f32dad649c73c6e34a3338bbf4` | 54513 |
| `/home/laoma/seo-workflow/data_sources/modules/write_pre_check.py` | `docs/legacy-skills/data_sources/modules/write_pre_check.py` | referenced frozen script | `46575b268635e2b908e70197e4f3fcf907c4d6ef3fe8244914d716c357282640` | 20371 |
| `/home/laoma/seo-workflow/data_sources/modules/content_scorer.py` | `docs/legacy-skills/data_sources/modules/content_scorer.py` | referenced frozen script | `777585808b6063a6de562bbbf97a2b2724b69793c867c463b2eeb5650187c1d1` | 40854 |

**Total: 8 files, 234577 bytes.**

---

## Why each script was included

Each file below is named or invoked in at least one of the two `SKILL.md` documents:

| Script | Referenced by | Skill doc line(s) |
|---|---|---|
| `research_collector.py` | `generate-prompt`, `collect`, `archive` commands | research/SKILL.md lines 14, 108, 137, 289 |
| `research_scorer.py` | Step 5 opportunity scoring | research/SKILL.md line 228 |
| `cannibalization_checker.py` | Step 1 蚕食预检 | research/SKILL.md line 148 |
| `write_collector.py` | `validate`, `post-process`, `register` commands | write/SKILL.md lines 15, 52, 59, 229, 237, 283 |
| `write_pre_check.py` | 段1b 预检 (15 mechanical checks) | write/SKILL.md line 325 |
| `content_scorer.py` | 段2 质量评分 (≥70 gate) | write/SKILL.md line 261 |

---

## Files NOT included (and why)

The following files exist under `/home/laoma/seo-workflow/` but were intentionally excluded per the snapshot rules (only rules/templates/scripts directly related to research, scoring, gating, writing, revision; no runtime data, keys, tokens, databases, logs, or customer data):

| Excluded path | Reason for exclusion |
|---|---|
| `/home/laoma/seo-workflow/.opencode/skills/plan/SKILL.md` | Planning skill is out of scope; only research and write were requested |
| `/home/laoma/seo-workflow/data_sources/modules/plan_collector.py` | Plan collector is out of scope |
| `/home/laoma/seo-workflow/data_sources/modules/plan_scorer.py` | Plan scorer is out of scope |
| `/home/laoma/seo-workflow/data_sources/modules/plan_feedback.py` | Plan feedback is out of scope |
| `/home/laoma/seo-workflow/data_sources/modules/seo_common.py` | Internal helper, not directly named in SKILL.md (only transitively imported by scripts) |
| `/home/laoma/seo-workflow/data_sources/modules/seo_config.py` | Internal config, not named in SKILL.md |
| `/home/laoma/seo-workflow/data_sources/modules/seo_quality_rater.py` | Quality rater is not invoked by SKILL.md |
| `/home/laoma/seo-workflow/data_sources/modules/content_scrubber.py` | Scrubber is invoked by `write_collector.py` internally but not named directly in SKILL.md |
| `/home/laoma/seo-workflow/data_sources/modules/readability_scorer.py` | Not named in SKILL.md |
| `/home/laoma/seo-workflow/data_sources/modules/gsc_importer.py` | GSC import tooling, not part of article workflow |
| `/home/laoma/seo-workflow/data_sources/modules/keyword_discovery.py` | Keyword tooling, not part of article workflow |
| `/home/laoma/seo-workflow/data_sources/modules/sync_blogs.py` | Sync tooling, runtime data layer |
| `/home/laoma/seo-workflow/data_sources/modules/sync_products.py` | Sync tooling, runtime data layer |
| `/home/laoma/seo-workflow/data_sources/modules/setup_website.py` | Site setup wizard, not part of article workflow |
| `/home/laoma/seo-workflow/data_sources/modules/README.md` | Module-level overview, not a rule or template |
| `/home/laoma/seo-workflow/data_sources/modules/__pycache__/` | Bytecode cache (runtime artifact) |

Context files (brand-voice.md, writing-examples.md, style-guide.md, seo-guidelines.md, target-keywords.md, internal-links-map.md, seo-data-manual.md) were also excluded because they are content/seed data, not rules or templates; their current versions already live in `data/legacy_workflow/laserpointerhub/context/` in the live system.

---

## Missing references (none)

Every file directly invoked by name in `research/SKILL.md` and `write/SKILL.md` was found at the original location and copied. No references were missing.

---

## Verification commands

The hash values above can be re-verified any time with:

```bash
cd docs/legacy-skills
sha256sum research/SKILL.md write/SKILL.md \
  data_sources/modules/research_collector.py \
  data_sources/modules/research_scorer.py \
  data_sources/modules/cannibalization_checker.py \
  data_sources/modules/write_collector.py \
  data_sources/modules/write_pre_check.py \
  data_sources/modules/content_scorer.py
```

Or against the originals:

```bash
cd /home/laoma/seo-workflow
sha256sum .opencode/skills/research/SKILL.md .opencode/skills/write/SKILL.md \
  data_sources/modules/research_collector.py \
  data_sources/modules/research_scorer.py \
  data_sources/modules/cannibalization_checker.py \
  data_sources/modules/write_collector.py \
  data_sources/modules/write_pre_check.py \
  data_sources/modules/content_scorer.py
```

Both runs should produce identical output, byte-for-byte.

---

## Provenance

- Snapshot created by: code review tooling (Claude Code, session 2026-07-27)
- Source directory SHA: not separately captured (each file's SHA-256 above is the authoritative hash)
- Sensitive-info scan: completed before commit; no secrets, keys, tokens, customer data, or production databases found in the 8 included files