# Generic intent-aware technical retrieval · 2026.09.15.32

This release hardens deterministic Stage 3 retrieval for unknown technical manuals. It does not add an answer-generating LLM and it does not contain manufacturer-, vessel-, crane-, engine-, or book-specific dictionaries.

## Generic query interpretation

The ranker separates the user's question into generic signals:

- **subject** — the equipment/topic being asked about;
- **intent** — definition/function, procedure, troubleshooting/alarm, or value lookup;
- **attribute** — voltage, pressure, temperature, torque, current, resistance, frequency, speed, flow, clearance;
- **identifier** — standalone technical tags/codes, distinguished from substrings inside longer codes.

A result is rewarded when the subject and requested evidence occur together. A procedure for the wrong subject is penalized even if its action words match perfectly.

## Procedure and troubleshooting

Procedure questions reward operational evidence such as check, measure, install, connect, operate, adjust, loosen, tighten, drain, open and close. Troubleshooting questions reward causes, checks, alarms/faults, normal/fault values, and corrective actions. Descriptive equipment text remains searchable but is not allowed to dominate a troubleshooting request merely through keyword overlap.

For audit, procedure/troubleshooting results include nearby Stage 3 chunks in `context_neighbors`; the normal UI keeps these inside **Source details**.

## Technical values

Value questions bind the subject and requested physical quantity to a compatible numeric unit. This prevents a chunk that only says “joystick in neutral” from outranking a chunk stating “approximately +6 V with the joystick in neutral”. The rule is generic across common engineering units.

## Technical identifiers and incidental numbers

Standalone identifiers retain the `.31` exact-boundary behavior. `.32` additionally distinguishes function questions from parts-list definitions and demotes incidental identifiers found in contact metadata such as phone/fax numbers.

## Cross-reference integrity

Both of these forms resolve to the same reference:

- `See instruction "High pressure pumps" in section 6.1`
- `See instruction under section 6.1 "High pressure pumps"`

Decimal section IDs are preserved. Follow-reference stays scoped to the originating book. When a parent question exists, the referenced title establishes the section anchor while the original query intent reranks nearby child chunks. This lets a procedure question land on the measurement/adjustment subsection rather than only the section title.

## Benchmarks

A benchmark question can now have multiple acceptable authoritative sources. Selecting **Use as expected** on another valid result for the same normalized question adds it to that case instead of creating a duplicate case. Top-1/3/5 succeeds when any acceptable source is retrieved.

## Immutability and model calls

Retrieval reranking changes no raw Docling JSON, converted ZIP, Stage 2C overlay, or Stage 3 source text. Search/follow-reference/benchmark operations make zero Docling/Pi5/OnePlus/Groq/LLM calls.

## Real seven-book replay

The `.32` ranker was replayed against the existing seven-book Stage 3 retrieval indexes with no model calls and compared with `.31` on the same data.

| Query | `.31` Top-1 | `.32` Top-1 |
| --- | --- | --- |
| What is 2141 refer to | Instruction Manual p.69 | Instruction Manual p.69 |
| Brake release procedure for luffing | MacGregor p.66 | MacGregor p.66 |
| How to check plussing pressure | Instruction Manual p.481 pressure table | **Instruction Manual p.186 measurement procedure** |
| How to zero set the anemometer | Deck/Hull p.284 oxygen analyser | **Anemometer p.8 zero setting** |
| What is the joystick neutral voltage | Instruction Manual p.88 neutral-position description | **Instruction Manual p.74 ~+6 V neutral output** |
| What to do for luffing limit alarm came | Instruction Manual p.59 limit table | **Deck/Hull p.103 luffing-not-working troubleshooting** |
| Procedure for oil filter change | MacGregor p.99 filter indicator | **MacGregor p.117 oil-filter-change procedure** |
| What valve 3221 do | Instruction Manual p.70 functional description | Instruction Manual p.70 functional description |

Following `High pressure pumps · section 6.1` while preserving the parent query `How to check plussing pressure` now ranks the actual pressure-gauge measurement procedure on page 186 above the generic section opening.

Full replay data: `docs/real-retrieval-replay-2026.09.15.32.json`.
