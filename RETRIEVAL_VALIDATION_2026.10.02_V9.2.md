# Retrieval v9.2 — relevance-first validation

Date: 2026-10-02

## Current production retrieval

- Rule version: `retrieval-relevance-first-v9.2`
- Source commit: `4d0218228d51e6cda2d359ea9ed12cb750fefe00`
- Updated file: `app/retrieval.py`
- GitHub retrieval/hybrid/scope regression gate: **63/63 passed**
- Patch SHA-256 checked before apply: `f086e60fd732a16104d2f6162c32d6444a3c79da13d65dc7497af51af16740d0`

## Relevance rule

A result is successful only when the citation itself answers or materially supports the question. Same-manual, nearby, or merely topic-related chunks do **not** count as correct.

For production answer generation, retrieve Top-5 candidates but cite only passages actually used to support the answer. Do not automatically cite every retrieved candidate.

## Final benchmark evidence

### Cleaned regression suite

- Valid questions: **2,754**
- Retrieval exceptions: **0**
- Relevance Top-1: **100%**
- Relevance Top-5: **100%**
- Deliberately stricter exact/support cross-check: **98.37% Top-1 / 99.35% Top-5**

This suite was inspected while developing v9.x, so it is a regression suite rather than an untouched generalization estimate.

### Fresh post-tuning holdout

- Previously unused evidence groups: **476**
- Retrieval exceptions: **0**
- Raw relevance: **97.90% Top-1 / 99.79% Top-5**
- One unsupported synthetic question (`BQ02933`, “Can you explain Operating current?”) was found: its source only says “Operating current is too low.” and does not define operating current.
- Valid holdout after excluding that unsupported item: **475 questions**
- Valid-holdout relevance: **98.11% Top-1 / 100% Top-5**
- No additional tuning was performed from this fresh holdout.

## Benchmark quality corrections

- Removed 11 header-generated `PARTS Description` pseudo-questions.
- Corrected/normalized 297 parts answer facts to the actual Article/Order/Part-number column.
- Added source-backed item/position context to 561 parts questions where bare descriptions were ambiguous.
- Added source-backed section context to 634 definition questions.
- Ambiguous questions that cannot identify one defensible citation must be excluded or rewritten; retrieval must not be tuned to guess an arbitrary duplicate row.

## Manual question types to test after deployment

Use both naturally phrased crew questions and precise manual-style questions. A citation passes only if it supports the requested fact/action.

1. **Definition / function**
   - What does valve 3221 do?
   - What is the purpose of the hoisting winch?
   - Explain the speed signal in the Description section.

2. **Procedure / how-to**
   - How do I calibrate this unit?
   - How do I adjust the control pressure?
   - What is the procedure to replace the filter element?

3. **Troubleshooting / symptom**
   - What should I check if operating current is too low?
   - Why is the crane not hoisting?
   - What does the manual recommend when pressure is too high?

4. **Exact value / specification**
   - What is the maximum permissible pressure?
   - What torque is specified for the locking nut?
   - What voltage/current/range is specified for this component?

5. **Maintenance interval / frequency**
   - How often should this filter be changed?
   - When is this inspection due?
   - What is the service interval for this component?

6. **Part / order / article / catalog number**
   - What is the order number for item 805, [component]?
   - What is the Article No. for the contact block in this assembly?
   - What catalog number is listed for this filter unit?
   Include item/position/assembly/tag context when the same description appears more than once.

7. **Identifier / tag / model / code**
   - What component is tagged `+CN.CT3-H12`?
   - Where is identifier `50130-4` referenced?
   - What does alarm/code X mean?

8. **Section-qualified lookup**
   - What does the manual say about the speed signal in the Description section?
   - Find the lubrication instruction under Maintenance.
   - What is specified for this component in the Technical Data section?

9. **Table lookup**
   - For item 4102, what part/order number is listed?
   - What quantity is specified for this spare?
   - Which row contains this component and its article number?

10. **Cross-manual / equipment-scope question**
    - Ask a Deck Crane question whose answer exists in only one of the two crane manuals and verify the citation comes from the correct manual.
    - Repeat for BESI equipment, where more than one manual competes in the same equipment scope.

11. **Natural crew shorthand**
    - `3221 valve function`
    - `filter unit order no`
    - `low operating current what check`
    - `locking nut torque`
    These are important because real users will not always ask full grammatical questions.

12. **Negative / no-answer question**
    - Ask something genuinely absent from the selected equipment/manual. The system should not present a merely related citation as though it answers the question.

## Manual acceptance rule

For every test question inspect the first citation and then Top-5:

- **PASS Top-1:** first citation directly supports the answer.
- **PASS Top-5:** first citation may be imperfect, but at least one Top-5 passage directly supports the answer.
- **FAIL:** citations only mention the subject, come from a nearby section, or are generally related without containing the requested answer/action/value.
- **INVALID QUESTION:** the corpus itself does not contain enough information to answer the question, or multiple duplicate rows are indistinguishable without missing context. Do not tune retrieval against such a question.
