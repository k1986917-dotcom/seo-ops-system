# tests/fixtures/legacy_pipeline/

> ⚠️ **All files in this directory are SYNTHETIC test fixtures.** They preserve the
> exact file structure, JSON schema, Markdown headings, and field names used by
> the real Legacy workflow, but the *content* is fictitious:
>
> - Site name: `examplesite` (real site is `laserpointerhub`)
> - Topic: `Example Topic for Testing the Legacy Pipeline` (no real article)
> - Slug: `example-topic-for-testing-the-legacy-pipeline` (no real article)
> - Product names: `EXAMPLE-001`, `EXAMPLE-002`, … (real SKUs are `B017`, `G019`, …)
> - All URLs, prices, search results, GSC keywords, score values are made up.
>
> Real samples from a successful Legacy run on 2026-07-27 live in
> `data/legacy_workflow/laserpointerhub/runs/action-2/` (gitignored) and were
> used as the structural reference for these fixtures. They are NOT included
> in the repo because:
>
> 1. `data/` is gitignored.
> 2. Some of the search-result text contains verbatim user quotes from
>    Reddit / Quora / forums; re-publishing them publicly requires attribution
>    that would be inappropriate for a fixture file.

## Directory layout

```
tests/fixtures/legacy_pipeline/
├── README.md                           # this file
├── action_sample/                      # what the SEO Ops DB looks like
│   └── actions.sqlite3.sql             # INSERT statements for actions + topics
├── context/                             # 7 context files (synced by legacy_sync)
│   ├── brand-voice.md
│   ├── writing-examples.md
│   ├── style-guide.md
│   ├── seo-guidelines.md
│   ├── target-keywords.md
│   ├── internal-links-map.md
│   └── seo-data-manual.md
├── published/
│   ├── published-index.json             # JSON index of every published article
│   └── example-published-article.md    # one desensitized published article
├── products/
│   └── live_products_report.md          # product spec table
└── workflow_run/                       # one successful end-to-end run
    ├── 01_search-prompt.md              # R0 output
    ├── 02_search-results.md             # R1 input (operator-pasted)
    ├── 03_research-data.md              # R1 output (human-readable)
    ├── 03_research-data.json            # R1 output (machine-readable)
    ├── 04_research-score.md             # R3 deterministic score
    ├── 05_material-pack.md              # R3 AI output (Part 1/2/3)
    ├── 06_research-brief.md             # R3 AI output (audit)
    ├── 07_draft.md                      # W0 output
    ├── 08_pre-check_report.md           # W1b output
    ├── 09_post-process_report.md        # W2 output
    ├── 10_register_report.md            # W3 output
    ├── 10_backlink-suggestions.md       # W3 AI output
    └── w2-state.json                    # gate verdict cache
```

## I/O relationships

The numbers in the filenames above (`01_`, `02_`, …) mirror the file order that
the Legacy workflow reads and writes them:

```
R0  stage_r0_generate_prompt
        reads: context/{brand-voice, writing-examples, ...}, published/, products/
        writes: workflow_run/01_search-prompt.md
                research/topic-context-{slug}.json

R1  stage_r1_save_and_collect
        reads: workflow_run/02_search-results.md   ← operator pastes here
        writes: workflow_run/03_research-data.md
                workflow_run/03_research-data.json

R3  stage_r3_ai_analyze
        reads: workflow_run/03_research-data.{md,json}
                research/topic-context-{slug}.json
                context/brand-voice.md
        writes: workflow_run/04_research-score.md   ← deterministic scorer runs first
                workflow_run/05_material-pack.md     ← then AI
                workflow_run/06_research-brief.md

W0  stage_w0_validate_and_draft
        reads: workflow_run/05_material-pack.md
                context/{brand-voice, writing-examples, style-guide,
                         seo-guidelines, target-keywords}
                published/internal-links-map.md
                products/live_products_report.md
                research/topic-context-{slug}.json
        writes: workflow_run/07_draft.md

W1b stage_w1b_pre_check
        reads: workflow_run/07_draft.md
        writes: workflow_run/08_pre-check_report.md

W2  stage_w2_post_process
        reads: workflow_run/07_draft.md
                workflow_run/05_material-pack.md
                products/live_products_report.md   (cannibal check target)
                published/*.md                     (cannibal check target)
        writes: workflow_run/09_post-process_report.md
                workflow_run/w2-state.json
                (with --apply) writes link fields into 07_draft.md frontmatter

W3  stage_w3_register
        reads: workflow_run/07_draft.md (post-apply)
                workflow_run/05_material-pack.md
        writes: workflow_run/10_register_report.md
                workflow_run/10_backlink-suggestions.md
                internal-links-map.md  (adds the new article)
                removes: workflow_run/05_material-pack.md  (archived to libraries)
```

Each `0N_*` file uses the same slug (`example-topic-for-testing-the-legacy-pipeline`)
and date (`2026-01-15`) so a tester can `cp -r workflow_run/` into
`data/legacy_workflow/examplesite/runs/action-99/current/laserpointerhub/`
and the UI will render it.

## Which fixtures come from real runs

After auditing the local working copy:

- **Real successful end-to-end run** (2026-07-27, action #2 "laser-pointer-for-pointing-above-ceilings-in-commercial-construction-…"):
  search-prompt, search-results, research-data (md+json), research-score, material-pack, brief, draft, pre-check report, post-process report exist on disk.
  These files are NOT shipped in this fixture (see the warning at the top of this README).
  When testing, copy them from `data/legacy_workflow/laserpointerhub/runs/action-2/current/laserpointerhub/`
  to `data/legacy_workflow/examplesite/runs/action-99/current/laserpointerhub/` and adapt.

- **Synthetic fixtures** (this directory): every file in `workflow_run/`,
  `published/`, `products/`, `context/`, and `action_sample/` is hand-written
  from scratch using the real file shapes as templates. They are NOT real
  AI output and NOT real search results; they exist so tests have something to
  assert against without depending on operator-pasted data.

If a future Codex task needs a verified fixture for a specific stage that
doesn't exist here, run that stage against the real data once, copy the output
into `workflow_run/`, scrub any user-quoted text, and replace the site slug +
URLs with the synthetic equivalents. Mark the file as `<!-- REAL-SAMPLE — DO NOT
EDIT — verbatim from <date> -->` at the top.