# Priority Retrieval Benchmark Audit — 133 Questions

## Ground-truth validation

The final priority benchmark contains **133 questions**:
- 53 priority-manual questions retained from the original 121-item set.
- 80 additional priority-only questions.

All 133 expected chunk IDs, source filenames, post-process job IDs and expected page lists were checked against the supplied current processed retrieval indexes. No invalid ground-truth target was found.

No duplicate benchmark IDs or duplicate query strings were found.

### Manual distribution
- Anemometer-Anemoscope.pdf: **13**
- MacGregor_Crane_Instruction_Manual.pdf: **36**
- Instruction Manual.pdf: **50**
- 5. AD136TI Operation and maintenance manual.pdf: **34**

### Category distribution
- troubleshooting: **23**
- procedure: **41**
- cross_reference: **11**
- technical_value: **43**
- definition: **8**
- confusion: **7**

## Current `.38` retrieval results

| Corpus | Top-1 | Top-3 | Top-5 | Top-10 | MRR |
|---|---:|---:|---:|---:|---:|
| All 7 books | 63.2% | 81.2% | 84.2% | 89.5% | 0.7251 |
| Priority 4 only | 63.9% | 85.7% | 87.2% | 92.5% | 0.7424 |

Removing the three deprioritized books improves Top-3/Top-5 more than Top-1. Top-1 rises only from **63.2% to 63.9%**.

## Priority-4 performance by manual
- **5. AD136TI Operation and maintenance manual.pdf** — n=34, Top-1 70.6%, Top-3 85.3%, Top-5 88.2%, MRR 0.7755
- **Anemometer-Anemoscope.pdf** — n=13, Top-1 76.9%, Top-3 100.0%, Top-5 100.0%, MRR 0.859
- **Instruction Manual.pdf** — n=50, Top-1 52.0%, Top-3 82.0%, Top-5 84.0%, MRR 0.67
- **MacGregor_Crane_Instruction_Manual.pdf** — n=36, Top-1 69.4%, Top-3 86.1%, Top-5 86.1%, MRR 0.7698

## Priority-4 performance by category
- **confusion** — n=7, Top-1 85.7%, Top-3 100.0%, Top-5 100.0%, MRR 0.9048
- **cross_reference** — n=11, Top-1 54.5%, Top-3 63.6%, Top-5 63.6%, MRR 0.6326
- **definition** — n=8, Top-1 37.5%, Top-3 100.0%, Top-5 100.0%, MRR 0.6667
- **procedure** — n=41, Top-1 58.5%, Top-3 90.2%, Top-5 92.7%, MRR 0.7321
- **technical_value** — n=43, Top-1 74.4%, Top-3 81.4%, Top-5 83.7%, MRR 0.7786
- **troubleshooting** — n=23, Top-1 60.9%, Top-3 87.0%, Top-5 87.0%, MRR 0.7228

## Wrong-book diagnosis

Across all seven books, **23** questions have a different book at rank 1.
With only the four priority manuals, **20** still have a different priority manual at rank 1.

The remaining priority-manual wrong-book pairs are:
- Expected `Instruction Manual.pdf` → rank-1 `MacGregor_Crane_Instruction_Manual.pdf`: **13**
- Expected `MacGregor_Crane_Instruction_Manual.pdf` → rank-1 `Instruction Manual.pdf`: **3**
- Expected `MacGregor_Crane_Instruction_Manual.pdf` → rank-1 `5. AD136TI Operation and maintenance manual.pdf`: **2**
- Expected `Instruction Manual.pdf` → rank-1 `5. AD136TI Operation and maintenance manual.pdf`: **1**
- Expected `5. AD136TI Operation and maintenance manual.pdf` → rank-1 `MacGregor_Crane_Instruction_Manual.pdf`: **1**

This means the main remaining problem is not the three textbook-style books. The dominant issue is discrimination between the two crane manuals, especially `Instruction Manual.pdf` and `MacGregor_Crane_Instruction_Manual.pdf`.

Several benchmark questions also describe procedures that are duplicated or nearly duplicated across both crane manuals. Those cases should either:
1. declare both genuinely equivalent chunks in `acceptable_sources`, or
2. include a distinguishing equipment/manual identifier when the benchmark intends to require one specific crane manual.

Do not weaken cross-manual safety by blindly accepting every similar chunk. Only mark alternatives acceptable after confirming the procedure/facts are materially equivalent.

## Recommended next retrieval work

1. Add manual/equipment identity evidence to ranking rather than relying only on generic subject terms.
2. Review the remaining Instruction Manual ↔ MacGregor rank-1 swaps and separate true duplicates from unsafe wrong-equipment matches.
3. Add/strengthen exact identifier discrimination for valve/item/card/terminal identifiers.
4. Improve technical-value table ranking, especially when the requested engineering unit/value is in a terse table row.
5. Keep the 133-question set frozen while tuning so before/after metrics remain comparable.
6. After retrieval tuning, rerun grounded answer generation on the previously wrong-book cases with `[S#]` + `[V#]` evidence safety enabled.

## Benchmark quality

The 80 newly added priority questions are valid against the current processed indexes. Only a small minority reuse four consecutive words from their target text, mostly fixed technical labels/identifiers, so the set remains predominantly paraphrased rather than copy-matched.
