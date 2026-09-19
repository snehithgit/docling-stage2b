# Processed Corpus Quality & Retrieval Audit

**Date:** 2026-09-19  
**Input:** `_processed (9)(1).zip`  
**Code reference:** Docling Visual RAG `.40.4.2`  
**Purpose:** Determine whether troubleshooting quality should be improved primarily in retrieval or in earlier pipeline stages.

---

## Executive decision

Do **not** change the BGE embedding model yet.

The current evidence shows three higher-value problems:

1. **Stage 2C automatic source-image corrections can delete valid troubleshooting actions, identifiers, or technical values.**  
   This is the most important upstream issue because retrieval cannot recover information that Stage 2C removed from the corrected source.

2. **Retrieval artifacts can remain "fresh" after application/retrieval-rule upgrades.**  
   Several manuals in this export still use older retrieval rules because freshness is tied to Stage 2C content changes, not to the retrieval-rule/software version.

3. **Synthetic reconstructed table evidence can crowd the Top-K.**  
   Table reconstruction improves semantics, but multiple `TBL-*` rows from the same table/parts-list family can occupy several result slots and push the expected canonical evidence down.

The 260 artifact jobs still pending at runtime are **not treated as failures** in this audit. Let them finish. A final visual/hybrid acceptance benchmark should be run only after the queue drains and downstream Stage 2C → Stage 3 → retrieval → machine embeddings have settled.

---

# 1. Corpus inventory

Seven processed manuals are present:

1. Anemometer / Anemoscope
2. Engine Room Electrical Maintenance & Troubleshooting
3. Hydraulics for Mariners
4. Deck & Hull Electrical Maintenance & Troubleshooting
5. MacGregor Crane Instruction Manual
6. Instruction Manual
7. AD136TI Operation & Maintenance Manual

Archive/source integrity checks found no missing source artifacts and the PDF/page manifests are structurally present.

---

# 2. Retrieval benchmark findings

## 2.1 The benchmark result currently stored in the processed export is misleading

The processed export contains only **4 saved benchmark cases**, not the frozen 133-question benchmark.

Those four cases do not contain `expected_equipment_id`. The stored lexical benchmark therefore searches across unrelated books and reports:

- Top-1: **25%**
- Top-3: **75%**
- Top-5: **75%**
- MRR: **0.50**

That result does **not** represent the current machine-scoped architecture.

When the same four questions are scoped to their actual machine/equipment, all four retrieve their correct source at **Top-1 (4/4)**.

### Required fix

The benchmark runner must never silently fall back to all-books search.

For legacy benchmark cases:

- infer a machine only when the expected source belongs to exactly one equipment scope;
- otherwise mark the case `UNSCOPED` / `NEEDS_EQUIPMENT_ASSIGNMENT`;
- do not score it as an all-books retrieval case.

New benchmark cases should persist `expected_equipment_id`.

---

## 2.2 Frozen 133-question lexical regression on the uploaded corpus

Using the existing frozen priority-manual benchmark and accepting either the expected source chunk or the expected source page:

| Metric | Current `.40.4.2` lexical |
|---|---:|
| Top-1 | **72.18%** |
| Top-3 | **88.72%** |
| Top-5 | **93.23%** |
| Top-10 | **95.49%** |
| MRR | **0.81053** |

This is broadly healthy for lexical retrieval and slightly better than the older `.39` lexical baseline.

### By category

| Category | Top-1 | Top-3 | Top-5 | Top-10 |
|---|---:|---:|---:|---:|
| Confusion | 85.71% | 100% | 100% | 100% |
| Cross-reference | **63.64%** | **63.64%** | 81.82% | 90.91% |
| Definition | 50.00% | 100% | 100% | 100% |
| Procedure | 70.73% | 95.12% | 100% | 100% |
| Technical value | 79.07% | 81.40% | 86.05% | **90.70%** |
| Troubleshooting | **69.57%** | **95.65%** | 95.65% | 95.65% |

### Interpretation

Troubleshooting has a **ranking problem more than a recall problem**:

- Top-1: 69.57%
- Top-3: 95.65%

In most troubleshooting cases the right evidence is already present very near the top. Large retrieval-model changes are therefore not justified yet.

The clearest ranking weakness is **cross-reference retrieval**, followed by exact technical-value/spec lookups.

---

## 2.3 Remaining frozen Top-10 misses

Representative misses include:

- CC/MC-card potentiometer readings
- cam-lobe wear limits
- intake/exhaust valve-stem diameters
- luffing-wire renewal cross-reference
- overheating test-pump / oil-cooler-fan troubleshooting
- maintenance intervals for heat exchanger / seawater-pump impeller / thermostat

These are good permanent regression cases for the next ranking pass.

---

# 3. Chunk and retrieval-index quality

## 3.1 The two electrical troubleshooting manuals are the weakest corpus

Original uploaded retrieval indexes:

| Manual | Total canonical chunks | Searchable | Excluded | Mean quality |
|---|---:|---:|---:|---:|
| Anemometer | 30 | 30 | 0 | 99.60 |
| Hydraulics | 697 | 691 | 6 | 99.32 |
| MacGregor Crane | 1,639 | 1,590 | 49 | 97.81 |
| AD136TI | 744 | 754* | 37 | 96.67 |
| Instruction Manual | 1,735 | 1,776* | 91 | 96.42 |
| Deck & Hull Electrical | 2,511 | 2,112 | **399** | **91.12** |
| Engine Room Electrical | 4,257 | 3,327 | **930** | **87.76** |

`*` Searchable count can exceed canonical chunk count because derived reconstructed table evidence is included.

Dominant exclusions in the electrical manuals are fragmented/header-only table evidence:

- Engine Room Electrical: **836** old `TABLE_HEADER_ONLY` exclusions
- Deck & Hull Electrical: **363**

These manuals are heavily troubleshooting-oriented, so their chunk structure matters more to troubleshooting performance than another generic embedding-model change.

---

## 3.2 Several manuals still use older retrieval rules

When the current `.40.4.2` retrieval enrichment logic is replayed on the existing canonical chunks, it creates useful table reconstruction/header-anchor artifacts for manuals that currently have none.

The problem is the freshness model:

- Stage 3 freshness currently checks the Stage 2C input signature and artifact existence.
- It does **not** include the Stage-3/retrieval-rule implementation version.

Therefore an application upgrade can introduce better retrieval reconstruction while an old book remains marked ready.

### Required fix

Persist and validate, separately:

- `stage3_rule_version`
- `retrieval_rule_version`
- optionally `embedding_rule_version`

If only retrieval rules change:

```text
Stage 3 canonical chunks remain valid
        ↓
derived retrieval artifacts become STALE
        ↓
rebuild retrieval artifacts
        ↓
machine embedding becomes STALE
        ↓
rebuild machine embedding
```

No Docling or Stage 2B model rerun should be required for a retrieval-only rule change.

---

# 4. Critical upstream finding: Stage 2C can remove valid source information

This is the highest-priority troubleshooting-quality issue found in the export.

Several `text_correction = applied` entries lose information present in the immutable source/previous text.

Confirmed examples include:

### Troubleshooting remedy removed

Original content included:

> Short circuit  
> Check output cable is not connected to 0V (ground)  
> Replace the MC card

Accepted reconstruction retained the cable check but dropped:

> Replace the MC card

Other confirmed cases similarly lose:

- `Replace the MEM card`
- `Replace the MC card`

This directly removes troubleshooting actions.

### Technical calculation/value truncated

An Engine Room formula reconstruction drops the final calculated value:

`= 133000 (pulses)`

even though the safety profile records changed critical numeric tokens.

### Parts/spec parent row removed

A MacGregor reconstruction removes a parent parts-list row containing:

`LIFTING BLOCK SWL 45 tonnes`

while retaining child rows.

That is an exact technical-value retrieval hazard.

---

## 4.1 Why the current safety gate allows this

Current automatic source-transcription safety permits changed critical tokens when similarity/alignment remain above combined thresholds.

That is too permissive for technical manuals.

A reconstruction can be textually "similar" while deleting the one action/value that matters operationally.

---

## 4.2 Required Stage 2C safety changes

### A. Critical-token preservation

For automatic source-image transcription:

```text
critical_tokens_changed = true
        ↓
DO NOT auto-apply
```

unless a deterministic normalization proves equivalence, e.g. formatting-only normalization such as `cm 2` → `cm²`.

Keep original text or route to review.

### B. Troubleshooting action-clause preservation

Detect operational clauses such as:

- check
- inspect
- verify
- replace
- reset
- open
- close
- clean
- tighten
- measure
- disconnect
- reconnect

If the original contains multiple remedy/action clauses, the proposed correction must not silently remove one.

### C. Table/parts-list row preservation

For table-cell / parts-list source types:

- never auto-apply a reconstruction that removes a non-empty parent row;
- never drop part numbers, item numbers, SWL/capacity values, alarm codes, wire IDs, limits, or units;
- prefer derived retrieval reconstruction over wholesale replacement of a dense table.

### D. Completeness check

Reject automatic source replacement when:

- significant non-whitespace prefix/suffix disappears;
- the result ends as a dangling/incomplete sentence;
- information density falls materially without source-boundary proof.

### E. Legacy-ledger revalidation

After strengthening the safety predicate, replay it deterministically over already-applied automatic correction ledger entries.

Do **not** rerun the LLM merely for this check.

- human-verified corrections remain authoritative;
- unsafe automatic corrections should be demoted and immutable original/previous source restored for downstream rebuilding.

---

# 5. Existing unresolved Stage 2C review items

Across the seven ledgers:

| Type/status | Count |
|---|---:|
| Vision enrichment applied | 1,045 |
| Text correction applied | 400 |
| Vision enrichment pending | 217 |
| Text correction pending | 188 |
| Vision enrichment excluded | 25 |

Main pending reasons:

- `VISION_UNCERTAIN`: 217
- `SOURCE_IMAGE_UNREADABLE_KEEP_ORIGINAL`: 183
- `TABLE_CELL_CONTEXT_CONTAMINATION_KEEP_ORIGINAL`: 4
- `SCOPE_INVERSION_DISCARDED_TARGET_KEEP_ORIGINAL`: 1

These are separate from the **~260 currently pending artifact-sweep jobs** reported by the user.

Pending/uncertain items correctly preserve the source; they are less dangerous than an incorrect correction marked `applied`.

---

# 6. Table reconstruction: useful, but Top-K needs diversity control

Running current reconstruction on older manuals adds useful `TBL-*` evidence.

However, in some parts-list queries, many synthetic table rows from similar pages occupy several Top-K positions.

Example behavior:

```text
rank 2  TBL...
rank 7  TBL...
rank 8  TBL...
rank 9  TBL...
rank 10 TBL...
```

The correct canonical/source table can then be pushed below Top-10.

### Required retrieval fix

Add result clustering/diversity after lexical/vector fusion:

- group evidence sharing the same `table_ref`, reconstruction source cluster, or near-identical source chunks;
- allow the best representative into the early Top-K;
- keep alternative rows available below it;
- do not let five synthetic variants of the same structural table consume five valuable result positions.

This should be benchmarked before mass-refreshing every old retrieval index.

---

# 7. Persisted job-ID integrity mismatch

Several result directories/manifests and Stage 2C/Stage 3 state files disagree about `postprocess_job_id`.

Examples observed include mappings where:

- directory/source manifest/retrieval index identify one job;
- Stage 2C or Stage 3 state metadata contains an older different job ID.

Retrieval currently works because most source resolution uses the directory/catalog information, but this is an audit/API/UI integrity risk.

### Required fix

Use the result directory/source manifest as the authoritative persisted book identity and validate all downstream state against it.

If downstream metadata disagrees:

- mark `IDENTITY_METADATA_MISMATCH`;
- repair only when the source manifest and directory identity agree unambiguously;
- never silently attach a book to another job/equipment.

---

# 8. Equipment coverage

The current equipment registry contains machine assignments for only:

- Anemometer
- Deck Crane / Instruction Manual

The other processed manuals are not currently assigned to a machine scope.

This means they cannot participate in normal machine-hybrid RAG until deliberately assigned.

Do **not** infer that similarly named crane/electrical manuals belong to the same physical machine.

If some are general reference/training manuals rather than machine-specific manuals, that is a separate future design question; they should not silently become authoritative machine evidence.

---

# 9. What to improve first

## P0 — before more retrieval tuning

### 1. Stage 2C automatic-correction safety
Prevent loss of remedies, values, identifiers, table rows, and calculation results.

**Reason:** retrieval cannot recover information removed upstream.

### 2. Retrieval-rule version freshness
Ensure books made with older retrieval rules are automatically marked retrieval-stale after an upgrade.

**Reason:** current code improvements are not automatically reaching all existing processed books.

### 3. Benchmark machine-scope migration
Remove legacy all-books benchmark behavior.

**Reason:** the current 25% stored Top-1 is misleading; correctly scoped, the same four cases are 4/4 Top-1.

---

## P1 — retrieval quality

### 4. Synthetic table-result diversity/collapse
Prevent reconstructed evidence from occupying many Top-K slots.

### 5. Cross-reference ranking
Current frozen benchmark:
- Top-1 63.64%
- Top-3 63.64%

This is the weakest retrieval category.

### 6. Technical-value/spec ranking
Focus on exact limits, intervals, dimensions, settings, and article numbers.

### 7. Electrical troubleshooting holdout
Create a new holdout set specifically from Engine Room and Deck/Hull electrical fault-finding tables.

Do **not** modify the frozen 133 set; keep it for regression continuity.

---

## P2 — extraction/verification refinements

### 8. Dense-table crop strategy
For large source tables, isolate the actual target row/cell rather than asking a small vision model to reconstruct a broad dense crop.

### 9. Truncation-aware retry
Where source transcription ended because of output length, retry only with a smaller target crop / row crop rather than simply raising max tokens globally.

### 10. Continue artifact sweep
Allow the current pending artifact work to finish. It may improve visual evidence coverage and will naturally invalidate/rebuild downstream artifacts if changes are applied.

---

# 10. Should the embedding model be changed?

**No evidence currently justifies changing BGE-small.**

Historical BGE hybrid evaluation substantially improved over lexical retrieval, and current lexical troubleshooting recall is already high at Top-3.

The current bottlenecks are:

1. source fidelity / Stage 2C safety;
2. derived-index freshness;
3. result diversity;
4. cross-reference and exact-value ranking;
5. benchmark scoping.

Changing the embedding model now would mix too many variables and could hide these structural issues.

---

# 11. Benchmark plan after fixes

When the pending artifact jobs finish and all downstream stages settle:

1. run the frozen 133 lexical benchmark;
2. run the fresh machine-scoped N150 BGE hybrid benchmark;
3. record Top-1 / Top-3 / Top-5 / Top-10 / MRR;
4. record per-category metrics;
5. run a new electrical troubleshooting holdout;
6. run targeted spec/table/SWL/value cases;
7. verify no `UNSCOPED` benchmark case is scored;
8. record latency and machine index fingerprint.

Keep these three metric classes separate:

- lexical fresh run;
- hybrid fresh N150 run;
- historical/deterministic candidate replay.

---

# 12. Recommended next release

Suggested release focus:

## `.40.5 — Source Fidelity & Retrieval Integrity`

Implementation order:

```text
Stage 2C source-preservation gates
        ↓
legacy applied-correction safety revalidation
        ↓
identity metadata integrity check
        ↓
retrieval-rule version freshness
        ↓
table-result diversity/collapse
        ↓
benchmark scope migration
        ↓
cross-reference / exact-value ranking
        ↓
new electrical troubleshooting holdout
        ↓
fresh N150 hybrid acceptance benchmark
```

This should improve real troubleshooting quality more than replacing the embedding model.
