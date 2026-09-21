## 2026.09.20.40.6.3 — Evidence arbitration & endpoint circuit breaker

- Fixes legacy normal-vs-sweep vision overlap arbitration: **applied evidence wins; the normal route wins only on an equal-status tie**. This preserves the 11 applied legacy sweep enrichments in the current processed snapshot that would otherwise be lost.
- Normal Stage 2B local endpoint connection failures now open a per-provider Pi5/OnePlus circuit breaker. Endpoint-down failures return the job to pending **without incrementing retry_count**, stop further claims for that provider, and health-probe with exponential backoff before resuming.
- One outage JSON is maintained per provider outage instead of one error artifact per failed job. Current jobs failed by older `ConnectError` retry exhaustion are requeued automatically at startup.
- Artifact sweep remains sequenced after normal Text/Vision. Books with zero normal routes release their prepared sweep immediately. `stage2b_artifact_sweep_required_for_finalize: false` allows Stage 2C/RAG to proceed while unfinished low-priority sweep rows remain; completed sweep results then intentionally make Stage 2C stale for rebuild.
- Stale-file deletion now requires the exact preview confirmation token server-side; a blind single POST can no longer delete a newly changed candidate set. Recovered endpoint-outage logs are also eligible for stale cleanup.
- `AppConfig` typing annotations in `stage2b.py` are now valid under static type checking via `TYPE_CHECKING`.
- **Upgrade note:** `.40.6.2/.40.6.3` overlap suppression changes current Stage 2B signatures for seven existing books. After deployment run **Revalidate all + rebuild** before relying on Machine RAG.
- Full regression suite: 436/436 tests.
- Full details: `docs/stage2b-outage-evidence-hardening-2026.09.20.40.6.3.md`.

## 2026.09.20.40.6.2 — Sequential artifact sweep orchestration

- Stage 2A route discovery now prepares `FULL_TECHNICAL_VISUAL` artifact-sweep rows automatically for every completed book.
- **Verify book** and normal Start actions authorize only Text/Vision work. The sweep is armed separately and becomes runnable only after every current normal Text/Vision route for that book is `completed`. Pending, processing, or failed normal routes keep the sweep blocked.
- Stage 2C therefore cannot finalize a book before its prepared artifact sweep has completed.
- Pictures already covered by a current normal picture route are skipped from the full sweep. Legacy overlapping sweep rows are made historical and matching Stage 2C vision enrichments are superseded instead of competing downstream.
- Artifact Audit **Verify all technical artifacts (backfill)** remains available for old/imported books, but it obeys the same Text/Vision completion gate.
- Full regression suite: 428/428 tests.
- Full details: `docs/sequential-artifact-sweep-2026.09.20.40.6.2.md`.

## 2026.09.20.40.6.1 — Stale derived-file cleanup

- Adds preview-first **Scan stale files** / **Clear stale files** maintenance for superseded verification errors/checkpoints, orphan equipment indexes, and outdated retrieval-quality artifacts.
- Active source manuals, current verifier results, canonical Stage 3 chunks, and active machine indexes remain protected.
- Deleting an equipment scope removes its matching equipment embedding index.
- Full details: `docs/stale-derived-file-cleanup-2026.09.20.40.6.1.md`.

## 2026.09.19.40.6 — Verification status breakdown

- Verification book rows now separate **Text**, **Vision**, and **Artifact sweep** work instead of mixing artifact jobs into the historical Vision/OnePlus lane.
- Every stage shows `completed/total` plus explicit pending, processing, and failed counts.
- **Verify book** now shows the exact remaining breakdown, for example `Text 2 · Vision 0 · Artifact 94`, while keeping the same execution behavior.
- Legacy Pi5/OnePlus counters and database rows are preserved; this is a status/UI correction only.
- Full details: `docs/verification-status-breakdown-2026.09.19.40.6.md`.

## 2026.09.19.40.5.1 — Convert queue renderer hotfix

- Fixes the Convert / Folder watcher page crash that showed `Cannot set properties of null (setting 'textContent')` instead of Recent documents.
- Root cause: the old optional sidebar failure badge `#failed-nav` was removed from the HTML but three scripts still wrote to it unconditionally.
- Dashboard, Manual Convert and Errors pages now treat that badge as optional; the Recent documents renderer is also defensive if its table body is unavailable.
- Regression coverage added so removed optional navigation elements cannot abort primary page rendering again.
- Full details: `docs/convert-queue-renderer-hotfix-2026.09.19.40.5.1.md`.

## 2026.09.19.40.5 — Source Fidelity & Retrieval Integrity

- Stage 2C automatic source-image corrections must preserve critical technical tokens, troubleshooting/remedy actions, complete formulas and dense table/parts rows. Unsafe automatic legacy overlays are deterministically demoted without rerunning an LLM; human-verified corrections remain authoritative.
- Stage 2C and retrieval artifacts now carry rule versions. A retrieval-only software upgrade refreshes derived retrieval artifacts from canonical Stage 3 chunks and then invalidates the affected machine embedding, without rerunning Docling/verification/chunking.
- Persisted job-identity metadata is audited against the authoritative result-directory identity and repaired only when unambiguous.
- Machine-scoped benchmark execution no longer silently falls back to all-books search. Legacy cases are scored only when a unique machine can be derived from the operator registry; otherwise they are skipped.
- Reconstructed table results are diversified so many synthetic rows from one table cannot crowd early Top-K results.
- Cross-reference and maintenance/value ranking are conservatively hardened while preserving the frozen 133 Top-1 baseline.
- Adds a frozen 12-case electrical troubleshooting holdout for future equipment-scoped acceptance once those manuals are assigned to real machines.
- Full details: `docs/source-fidelity-retrieval-integrity-2026.09.19.40.5.md`.

## 2026.09.19.40.4.2 — Shared Artifact Work-Stealing Hotfix

- Full technical-artifact sweeps use one shared local Pi5 + OnePlus pending pool. Each physical worker pulls the next artifact only when it is idle; there is no fixed 50/50 or weighted split.
- A slow OnePlus naturally receives fewer jobs because it cannot claim another until its current request finishes. Pi5 behaves the same way.
- Transport/server failures put only the failing artifact worker into a short cooldown; the other local worker continues draining the queue and may claim the retry.
- A manually paused worker is never silently unpaused by Start/Retry. This lets the operator keep a hot OnePlus out of the artifact pool while Pi5 continues alone.
- Normal Stage 2B Vision verification remains single-provider and follows the explicit Pi5 / OnePlus / Groq selection with no automatic fallback. The shared local pool applies only to `FULL_TECHNICAL_VISUAL` artifact sweeps.
- Shared-pool claims are atomic, preventing Pi5 and OnePlus from processing the same artifact simultaneously.

## 2026.09.19.40.4.1 — Vision Provider Routing Hotfix

- Fixes full technical-artifact vision sweeps ignoring the selected Vision processor.
- Pi5 / OnePlus / Groq selection is now authoritative for every normal and full-sweep vision job; there is no hidden per-job processor override or automatic fallback.
- New full-sweep jobs use one Vision-role queue. Legacy queued sweep rows from <= `.40.4` are still accepted but their stored `source.processor` hint is ignored at execution, so upgrades do not require reconversion or requeueing.
- Artifact Audit UI now reports the selected Vision processor instead of claiming Pi5 + OnePlus round-robin load sharing.

## 2026.09.18.40.4 — Retrieval Structural Hardening

- Extends conservative table reconstruction across adjacent page boundaries when Stage 3 chunks remain contiguous and share the same Docling table reference.
- Adds derived table-continuation/header anchors so label-less rows can retain their nearest safe table semantics without rewriting canonical chunks.
- Adds technical-prose detection for conditional faults, checks/remedies and imperative procedures alongside numeric/tolerance signals.
- Adds deterministic per-page OCR-risk and a 0–100 review priority score; Stage 2B now orders same-band work by technical/risk priority.
- Adds structured-ID subject weighting and a BGE semantic-intent tie-breaker without a per-query LLM call.
- Keeps one authoritative embedding corpus per physical machine while reusing unchanged vectors during rebuilds and embedding only changed/new rows before atomic replacement.
- Adds manual revision/authority groundwork: Current revisions participate in Machine RAG; Historical/Draft revisions remain auditable but are excluded from normal machine retrieval.
- Saved benchmark cases can carry an equipment scope, and the UI/API includes an explicit fresh machine-hybrid benchmark using the configured N150/TEI BGE service.
- Full release details: `docs/retrieval-structural-hardening-2026.09.18.40.4.md`.

## 2026.09.18.40.3 — Sequential Machine RAG + Chunk Viewer + UX hardening

- Enforces the dependency chain `Docling -> 2A -> 2B -> 2C -> Stage 3 -> machine embeddings -> RAG`.
- Stage 2C/Stage 3/machine embeddings use input signatures so stale downstream data is not treated as ready.
- Production vectors are persisted as one embedding corpus per physical machine/equipment across all assigned current manuals.
- A configured machine is blocked until every assigned manual has current Stage 3 retrieval data.
- Adds `/chunks` Chunk Viewer with machine/manual search, full chunk provenance, previous/next navigation and original PDF page view.
- Machine RAG, My Books and Book Workflow now distinguish chunks-ready, machine assignment, embedding rebuild and true RAG-ready states.
- Full release details: `docs/ui-machine-rag-pipeline-2026.09.18.40.3.md`.

## 2026.09.18.40.2 — Retrieval scope safety hotfix

- Removed global `All books` RAG search and all null-scope fallbacks.
- Search/generate/prompt export/hybrid build require exactly one book or equipment scope.
- Hybrid readiness is now proactive per scope; unready scopes switch to Lexical only with a visible instruction.
- Equipment retrieval is blocked until all assigned manuals have text indexes, preventing partial-equipment search.
- See `docs/retrieval-scope-safety-hotfix-2026.09.18.40.2.md`.

## 2026.09.18.40.1 — Navigation and keyboard-accessibility hotfix

- Ready books now keep an unconditional **Open** link to `/book?job=<id>` and expose **Test RAG** separately as `/retrieval?job=<id>`.
- RAG quality reads the `job` query parameter once and pre-selects the requested book scope.
- Source-page modal moves focus to its close button, traps Tab inside the modal, and restores focus to the triggering source button on close.
- Mobile navigation moves focus into the drawer, traps Tab while open, supports Escape, and restores focus to the hamburger trigger.
- `/book` without a job id now shows a direct clickable **Return to My books** link.
- Every static page now has a keyboard-visible **Skip to main content** link and a `#main-content` target.
- Asset version bumped to `.40.1` to avoid stale-browser cache of the broken `.40` UI.

## 2026.09.18.39 — Equipment-scoped multi-manual retrieval

This phase changes the normal RAG boundary from the full library / individual PDF to the physical equipment. One equipment can own description, operation, maintenance, electrical, hydraulic, parts, tools and other manuals while each manual keeps full source provenance.

- persistent equipment registry in `processed_dir/equipment_registry.json`;
- create/edit/delete equipment groups from RAG quality;
- one manual can belong to only one equipment group;
- equipment-scoped `[S#]` text and `[V#]` visual retrieval;
- grounded generation may combine manuals only inside the selected equipment;
- single-book mode retained; all-books mode retained as diagnostic only;
- no embeddings yet — hybrid vectors are the next controlled phase;
- bundled OnePlus script updated to the user-confirmed working `$HOME/models` version.

See `docs/equipment-scoped-retrieval-2026.09.18.39.md`, `docs/PROJECT_TRACKER.md`, `docs/PROJECT_ACQUIRED_STATE.md`, and `docs/PROJECT_IMPLEMENTATION_TODO.md`.

# Docling Auto-Convert

## 2026.09.16.38 — Visual evidence for RAG

- Converts completed `.37` Stage 2C `vision_enrichment` records into deterministic `visual_evidence.jsonl`, `visual_evidence_index.jsonl`, and a summary without any new Docling/vision/model call.
- RAG now uses **[S#] text evidence + [V#] visual evidence**. Ordinary questions remain locked to the anchor book; explicit compare/across-manual questions may use cross-book evidence.
- Only applied, resolved technical visuals are automatically RAG-eligible. Uncertain/unresolved/decorative/failed visual records remain auditable but are not trusted as technical evidence.
- `Copy for other LLM` exports the same mixed `[S#]/[V#]` evidence packet. Generation prompts distinguish model-read `visible_text` from interpretive visual summaries/objects and forbid promoting interpretation alone into exact technical facts.
- Procedure/troubleshooting context is no longer expanded by chunk adjacency alone; a neighbor must share a Docling item or the same deepest heading/section.
- Artifact Audit exposes each `[V#]` record ID, eligibility state/reason, and the existing source/provenance.
- Fixes the Pi5-picture edge case so source type, not physical worker name, decides whether a result belongs to Text or Vision audit/ledger semantics.
- Raw Docling remains immutable and there is still no automatic provider fallback.

See `docs/visual-rag-evidence-2026.09.16.38.md`.

## 2026.09.16.37 — Full technical artifact sweep

- **Verify all technical artifacts** now creates fresh Stage 2B vision jobs for every Docling picture classified as a technical visual: engineering drawings, flow charts, screenshots from manuals, tables, charts, full-page images, and geographical maps.
- The sweep re-verifies the full technical-visual universe, including pictures that had older low-confidence routes. On the supplied seven-book dataset this universe is **1,395 technical visuals** out of 2,760 pictures.
- Fresh artifact jobs are deterministically split between the two local vision workers: **Pi5 + OnePlus**. Each device keeps concurrency 1 and both workers can run in parallel.
- Pi5 picture jobs use the vision path, not the text-correction path. Stage 2C stores them as `vision_enrichment` entries exactly like OnePlus picture jobs.
- Artifact audit and Vision audit now recognize picture jobs completed by either local worker. Text audit excludes Pi5 jobs whose source is a picture.
- Re-running the full sweep is idempotent: `AVxxxxxx` artifact routes are inserted with `INSERT OR IGNORE`, so pressing the button again resumes/re-authorizes instead of duplicating the same worker assignment.
- **Retry failed artifact queue** retries only full-sweep picture jobs on both local workers; it does not restart unrelated text verification failures.
- Raw Docling ZIPs remain immutable. Existing `.36` artifact thumbnails/audit output, `.35` grounded generation, Stage 3 chunks, and retrieval indexes remain intact.

See `docs/full-technical-artifact-sweep-2026.09.16.37.md`.

## 2026.09.16.36 — Artifact audit foundation

- Adds a new **Artifact audit** page to inventory every Docling picture artifact across processed books.
- Each artifact card shows the thumbnail, page, Docling picture class/confidence, whether it is a technical candidate, current vision-queue status, and the Stage 2C output/summary when available.
- Adds direct artifact image serving from the immutable converted Docling ZIP so the audit can display the exact source picture even when no verifier job exists yet.
- Adds queue helpers for the current vision queue: **Start pending artifact queue** and **Retry failed artifact queue**. These only authorize existing vision jobs; they do not create new routes or rerun Stage 2A.
- Existing text audit, vision audit, retrieval, Stage 3, and grounded-answer behavior from `.35` remain unchanged.

See `docs/artifact-audit-foundation-2026.09.16.36.md`.

## 2026.09.15.35 — Generation evidence safety

- Fixes a real `.34` failure where an anemometer question could receive a crane zero-setting procedure simply because both results used the words “zero setting”.
- Answer generation and **Copy for other LLM** now use a conservative evidence packet: unless the question explicitly asks to compare/across manuals, evidence is locked to the **Top-1 source book**.
- Procedure/troubleshooting adjacent Stage 3 chunks are inserted into the evidence packet before weaker same-book hits so split instructions can be recovered without crossing equipment/manual boundaries.
- The exact evidence scope is shown below generated answers. Adjacent evidence is labeled `adjacent`; every source keeps `+ Page` provenance.
- Explicit comparison/across-manual questions may still use cross-book evidence. Ordinary maintenance questions do not silently mix books.
- The grounding prompt explicitly forbids transferring procedures, values, settings, or troubleshooting steps between different equipment merely because wording overlaps.
- A correct `Not enough information in the retrieved sources.` response is treated as a safe stop and no longer gets a misleading missing-citation warning.
- Local generator connection failures now identify the selected server URL and tell you to check llama.cpp/IP/port instead of only reporting `All connection attempts failed`.
- Retrieval ranking, Stage 3 chunks, correction overlays, Docling output, and OnePlus llama controls are unchanged. Existing indexes are reusable.

See `docs/generation-evidence-safety-2026.09.15.35.md`.

## 2026.09.15.34 — Grounded answer generation

- Adds optional answer generation directly on **RAG quality** after deterministic retrieval. Search still makes zero LLM calls until **Generate answer** is pressed.
- Generator selection is explicit: **Pi5**, **OnePlus**, or **Groq Cloud**. The selected provider is the only provider called; there is no automatic fallback.
- Answers receive only the retrieved Stage 3 source chunks and are instructed to use inline `[S1]`, `[S2]` citations, preserve technical numbers/units/identifiers, report conflicts, and say when the retrieved evidence is insufficient.
- Pi5/OnePlus answer generation shares the same provider lock used by Stage 2B so a manual RAG answer cannot collide with verification on the same physical server.
- Groq answer generation reuses the existing API-key configuration and free-tier quota/usage guard.
- **Copy for other LLM** makes no model call. It copies the question, grounding rules, full retrieved chunks, book/page/chunk metadata, Docling provenance refs, and stable `[S#]` labels so the evidence can be pasted into ChatGPT, Claude, Gemini, or any other LLM.
- Generated answers show the exact evidence sent to the model and keep `+ Page` access to the original PDF. Missing/invalid citation labels are surfaced as a grounding warning rather than silently trusted.
- No Docling, Stage 2A/2B/2C, Stage 3, correction, or retrieval-ranking behavior changes from `.33`/`.32`.

See `docs/grounded-answer-generation-2026.09.15.34.md`.

## 2026.09.15.33 — OnePlus llama-only control

- Removes the rooted OnePlus charging controller, Magisk `service.d` charging script, charging APIs, charging UI, and charging-specific tests/docs.
- The OnePlus page is now intentionally narrow: SSH/script/server status plus **Start / Restart / Stop** for the phone-side llama.cpp server.
- One-time installation/update of `$HOME/bin/oneplus-llama-control` remains available under a small setup disclosure.
- No OCR, verification, Stage 2C, Stage 3, or retrieval-ranking behavior changes from `.32`.

## 2026.09.15.32 — Generic intent-aware technical retrieval

- Generic query intent now distinguishes definition/function, procedure, troubleshooting/alarm, and technical-value questions without manufacturer or book-specific dictionaries.
- Subject-aware reranking prevents a perfect procedure for the wrong equipment from outranking the correct subject; book titles/headings can provide generic subject evidence.
- Technical-value queries bind subject + requested attribute + compatible unit/value (for example voltage, pressure, temperature, torque, current, resistance, frequency, speed, flow, clearance).
- Functional identifier questions prefer passages explaining what a component does and strongly demote incidental contact-number matches.
- Troubleshooting queries favor causes, checks, measurable normal/fault states, and corrective actions over descriptive mentions.
- Cross-reference parsing supports both title-first and section-first wording, preserves decimal section IDs, remains same-book by default, and inherits the parent question intent when ranking referenced child procedures.
- Procedure/troubleshooting results expose adjacent Stage 3 context under Source details for audit without changing source text.
- Retrieval benchmarks allow multiple acceptable authoritative sources for one question.
- No LLM, Docling, Pi5, OnePlus, or Groq call is introduced by retrieval ranking.

See `docs/generic-intent-aware-retrieval-2026.09.15.32.md`.

## 2026.09.14.31 — Generic technical retrieval audit

- Generic identifier-aware reranking distinguishes standalone equipment/tag IDs from substrings inside longer part numbers.
- Explicit manual cross-references such as `See instruction "High pressure pumps" in section 6.1` can be followed inside the same unknown book.
- Every retrieval result and followed reference has a `+ Page` source viewer using the original PDF page.
- When Docling provenance exists, the exact source item is outlined on the rendered page; no source file is modified.
- No manufacturer-specific dictionary or book-specific ranking rules are used.

See `docs/generic-technical-retrieval-audit-2026.09.14.31.md`.


## 2026.09.14.30 — Stage 3 / retrieval quality

This release keeps the `.29` KISS UI and `.28`/`.27` verification safety rules intact, then adds the next major layer: **RAG-ready Stage 3 chunks plus measurable local retrieval quality**.

- Stage 3 now recursively rechecks safe split children and compacts Markdown/table formatting noise without changing raw source text. On `_processed (10)`, oversized chunks fall from **627 to 2**, with only **1 meaningful oversized chunk remaining searchable**.
- Every Stage 3 build writes `retrieval_index.jsonl` and `retrieval_quality.json`. Obvious table-header/separator noise and strongly repetitive OCR fragments stay auditable in `chunks.jsonl` but are excluded from retrieval.
- The new **RAG quality** page searches the exact local chunks with a deterministic BM25-style ranker. It makes no LLM call.
- **Optimize + index all** upgrades existing Stage 3 outputs without rerunning Docling, Pi5, OnePlus or Groq.
- A user can mark a search result as the expected source and run a fixed retrieval benchmark with Top-1 / Top-3 / Top-5 / MRR metrics.
- A seven-book real-data smoke benchmark returned the expected book at Top-1 for **7/7** representative technical queries.

See `docs/stage3-retrieval-quality-2026.09.14.30.md`.

## 2026.09.14.29 — KISS UI/UX cleanup

This release keeps `.28` OCR/model/correction/chunking/charging behavior intact and concentrates on a simpler operator workflow. Two convenience actions are added without changing verification policy: read-only Text audit and bulk retry of already-failed verifier jobs.

- Primary navigation stays focused on **Books, Convert, Verification, Verifier audit, OnePlus, Settings**. Diagnostics remain secondary.
- **Books** is the operational home: compact totals, search/filter, one status per book, one main action.
- **Verification** keeps live Text/Vision controls visible and adds one compact **Retry all failed** button that requeues only failed current Text/Vision routes. Successful and pending jobs are untouched.
- **Verifier audit** is one area with **Text / Vision** tabs. Text now has the same read-only evidence trail Vision already had: exact crop, raw response, parsed/scoped result, deterministic safety gates, and Stage 2C downstream action.
- **OnePlus** is limited to the phone-side llama.cpp lifecycle; charging control is not part of the app.
- The current **server version is always visible in the sidebar**, including a UI/server mismatch hint for stale browser assets.
- Raw audit detail, Groq usage, setup controls, and explanations remain available through progressive disclosure instead of cluttering the normal workflow.
- No new OCR rules, provider fallback, correction policy, or chunking policy are introduced in `.29`.

# Docling Auto-Convert

**Docling Auto-Convert** is a document conversion and technical-manual verification dashboard for an existing Docling Serve installation. One Python container watches a bind-mounted input folder, queues eligible files in order, submits one conversion at a time through Docling Serve’s asynchronous REST API, persists status in a local SQLite file, and publishes live browser updates over Server-Sent Events.

Document storage, Docling conversion, Stage 2A analysis, corrections/overlays and chunk artifacts remain local. Verification provider choice is explicit and symmetric: **Text/OCR reconstruction** and **Image analysis** can each independently use **Pi5**, **OnePlus**, or **Groq**. Pi5 and OnePlus are both treated as vision-capable processors. There is no automatic provider fallback or rotation. Groq is optional. The project has no cloud storage, analytics, CDN assets, external database, or external queue service.

## What is included

| Area | Implementation |
| --- | --- |
| Automatic intake | A dedicated discovery loop watches the top level of the configured input directory even while a long conversion is running. It only creates a job after a file is stable; a separate single conversion worker processes queued files oldest-first. |
| Conversion | The worker uses `POST /v1/convert/file/async`, polls `GET /v1/status/poll/{task_id}`, then fetches `GET /v1/result/{task_id}`. |
| State | A single `jobs.db` SQLite file tracks pending, processing, completed, and failed documents, plus source size/mtime/SHA-256 identity so a changed file can be processed even when its filename is reused. Existing databases are migrated in place. |
| Interface | The overview dashboard, error log, settings drawer, and download links are plain bundled HTML, CSS, and JavaScript. |
| Recovery | A failure is recorded and never blocks later files. After restart, saved Docling task IDs are resumed instead of re-uploaded; a processing row that never received a task ID is safely returned to pending. The Error Log can re-queue failed input files. |
| Manual convert | A `/convert` page mirrors Docling Serve's own bundled options UI (Convert URL / Convert File, plus the full options panel) for one-off, on-demand conversions outside the folder-watch pipeline. |
| Retrieval + answers | RAG quality searches Stage 3 text `[S#]` plus normalized resolved visual evidence `[V#]`. Answer generation is optional and explicitly selectable between Pi5, OnePlus, or Groq; the same mixed evidence can be copied as a portable prompt with zero model calls. |


## Stage-wise book workflow · version 2026.09.18.39

Verifier status is always visible on Verification. **Text/OCR reconstruction** and **Image analysis** each expose the same explicit selector: **Pi5 / OnePlus / Groq**. The selected provider is persisted and is the only processor used for that role until you change it. There is no automatic provider fallback. Start, Stop and per-role Auto Run controls remain explicit. The dedicated OnePlus page controls the phone llama.cpp server whenever either role is assigned to OnePlus.

The primary UI is **My books → Open workflow**:

`Docling → 2A Extraction → 2B source-image reconstruction + image analysis → 2C automatic overlay/enrichment → 3 Hybrid chunks`

- A routed OCR text block is **not semantically judged**. The app uses the Docling provenance bbox to render only that target region from the original PDF/raster source. Neighboring Docling blocks are supplied as BEFORE/AFTER location context only and are deliberately kept outside the crop.
- The selected Pi5, OnePlus, or Groq vision-capable processor transcribes only the target crop. `READABLE` + unchanged text keeps Docling. `READABLE` + different text is applied only when deterministic target-alignment safety passes; wrong-region/low-overlap transcriptions, unsafe high-risk numeric/identifier changes, truncation, partial-target reconstruction and novel token duplication preserve immutable Docling instead. `UNREADABLE` also preserves Docling and records the item for audit.
- Whole-page OCR fallback is disabled by default. Missing/unusable target coordinates therefore preserve the original rather than asking a model to guess the target on a dense page.
- Human Review is **optional audit/manual override**, not a Stage 2C or Stage 3 gate. Human-verified edits still have highest precedence and cannot be overwritten automatically.
- Stage 2C can auto-finalize after all Stage 2B routes are terminal; Stage 3 still requires a finalized Stage 2C artifact.
- Image/figure routes remain separate from OCR text reconstruction and use the independently selected Image processor.
- Raw Docling ZIP/JSON is never modified.
- Version `.21` adds a deterministic target-scope filter after transcription. BEFORE/AFTER anchor text, duplicated model output, `<think>` residue and clearly unrelated adjacent-block captures are trimmed/rejected before any automatic overlay write.
- Isolated `rare_near_frequent_token` edit-distance matches are retained as **low-priority source-image verification routes** instead of being discarded. Strong same-document corroborators remain medium/high priority. Lexical similarity alone can never apply a correction.
- An automatic `status=applied` correction is already live in `chunk_overlays.jsonl`; the Audit page now opens with that applied text and explicitly says **No Save click is required**. Save is only a human override.

- Version `.22` hardens Stage 2A for unknown manuals: empty text and geometry defects, Docling graph integrity, Unicode/PUA anomalies, table-grid validation, table-cell OCR/recall, geometry-aware running furniture, cross-page word-break evidence, honest recall-cap accounting, and optional PDF↔JSON source cross-checking.
- Table-cell OCR corrections are supported end-to-end: Stage 2B crops the exact cell, Stage 2C records `(table_index, cell_index)`, and Stage 3 applies the overlay only to the in-memory Docling JSON before HybridChunker.
- Version `.28` adds one-click **Revalidate all + rebuild** maintenance for all eligible books: saved Text-verifier results are revalidated, Stage 2C is rebuilt, then Stage 3 is rebuilt sequentially without new Pi5/OnePlus/Groq verification calls.

Manual Text → Image re-read also uses the isolated target crop and the currently selected Image processor. Vision → Text consistency checking remains an optional audit action. Local OnePlus retains the slow-device-safe limits of 1200 seconds to first token and 300 seconds of stream idle.

## Manual convert page

Open `/convert` from the sidebar to convert a single URL or file on demand, independent of the watched-folder pipeline. It exposes the same per-request options as Docling Serve's own UI:

- **To Formats** — Docling (JSON), Markdown, HTML, Plain Text, Doc Tags (multi-select)
- **Watcher format snapshots** — choose any one/two/three/etc.; each queued file keeps exactly the formats selected when it was discovered, and Live Queue shows them all
- **Image Export Mode** — Embedded, Placeholder, Referenced
- **Pipeline type** — Legacy, Standard, Vlm, Asr
- **OCR** — Enable OCR, Force OCR, and an OCR Engine choice (Auto, EasyOCR, Tesseract, RapidOCR)
- **PDF Backend** — `docling_parse`, `pypdfium2`
- **Table Mode** — Fast, Accurate
- **Infer heading levels**, **Abort on Error**
- **Enrichment** — code enrichment, formula enrichment, picture classification, picture description

There is no "Return as File" toggle: this page always submits with a zip target and always ends with a `Download converted_docs.zip` link once the task succeeds, so that switch has nothing left to control. Submissions go through `POST /v1/convert/file/async` (for uploads) or `POST /v1/convert/source/async` (for URLs) on your configured Docling Serve instance, independent of the automatic pipeline's settings, and are not persisted to `jobs.db` — they're ephemeral, one-off conversions the same way Docling Serve's own UI works.

## Quick start on ZimaOS

First copy the configuration template and change `docling_url` to the LAN address of your existing Docling Serve box. The supplied settings screen can later update the Docling URL, input folder, output folder, and desired output format directly in this same configuration file.

```bash
cp config.example.yaml config.yaml
mkdir -p input converted data
docker compose up --build -d
```

Open `http://<ZimaOS-host>:8080`. Drop supported documents directly into `input/`; they are discovered and queued but, by default, **do not start Docling automatically**. On Overview press **Start queued files** to run the current queue, or enable **Auto Run** for continuous operation. Completed files are saved under `converted/`, while persistent state is saved in `data/jobs.db`.

## Output modes

The default `target_type: zip` writes the first version as `{filename-stem}.zip`. If a different source later reuses the same filename, its SHA-256 identity is used to preserve the earlier output and write a distinct name such as `{filename-stem}__a1b2c3d4.zip`. Change `target_type` to `inbody` if you prefer directly downloadable `.md`, `.json`, or `.text` files. The settings panel exposes watcher `to_formats` as a true multi-select. You can choose any one, two, three, or more supported formats; each discovered job snapshots that exact set and the live queue displays all selected formats. The panel does not change `target_type`, OCR, table mode, pipeline, timeouts, or extension allow-list, which intentionally remain explicit deployment configuration.

| Desired output | Set `to_formats` | Direct-output extension when `target_type: inbody` |
| --- | --- | --- |
| Markdown | `['md']` | `.md` |
| JSON | `['json']` | `.json` |
| Plain text | `['text']` | `.text` |

## Operational behavior

The conversion worker processes **exactly one document at a time**, while an independent discovery task continues scanning the input directory. The default execution mode is **Manual Start** (`watcher_auto_run: false`): files can accumulate safely as `pending` until **Start queued files** is pressed. A Start press snapshots only the files currently pending and processes that batch **smallest file first → largest file last**. Files discovered after the snapshot wait for the next Start. **Auto Run** can be toggled on from Overview; it is persisted and continuously selects the smallest pending file next. Turning Auto Run off lets the current Docling task finish, then pauses before another submission. Unsupported files are ignored. A failed conversion remains visible on the Error Log and is not automatically retried; retrying from the UI increments its retry count and reuses the same source identity.

Automatic uploads stream the source file from disk rather than calling `read_bytes()`, avoiding a full extra in-memory copy of large scanned manuals. New jobs store source size, nanosecond mtime, and SHA-256. Unchanged files use the cheap size/mtime fast path on later discovery passes, so retained large inputs are not re-hashed every few seconds. Older filename-only database rows are backfilled once during normal discovery.

Completed files are written to a hidden `.part` file, flushed to disk, and validated before `os.replace()` atomically exposes the final filename. ZIP results must be a readable, non-empty ZIP whose CRC test passes. An invalid or interrupted result never replaces a previously valid output.

The application checks Docling Serve’s `/health` and `/ready` endpoints frequently so the dashboard distinguishes a reachable-but-not-ready server from one that cannot be reached. If the app restarts while Docling is converting, a saved `docling_task_id` stays in `processing` and the worker resumes polling it. A processing row with no saved task ID is re-queued as `pending` because there is no remote task to resume.

## Local verification

Create an isolated environment, install the requirements, then run the included standard-library test suite and server.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
cp config.example.yaml config.yaml
uvicorn app.main:app --reload --port 8080
```

## Supported extensions

The default allow-list is declared in `config.yaml` and covers PDFs, Office files, CSV, text/HTML/Markdown, and common raster-image formats. The application only inspects files directly inside the input folder; it deliberately does not recurse into subfolders.

## API reference

The client follows the official Docling Serve REST API flow for asynchronous file conversion: upload using a `files` multipart field and form-based options, wait for a `task_status` of `success` or `failure`, then retrieve the result. See the [Docling REST API documentation](https://docling-project.github.io/docling/usage/api_server/rest_api/) for the full live schema and additional conversion options.


## Stage 2A: quality profiling and routing

Completed Docling ZIPs now automatically enter a separate, non-destructive quality queue. Results are written under `./processed` and can be reviewed at **http://HOST:8080/quality**. The raw converted ZIP is never modified.

Stage 2A produces `profile.json`, `diagnostics.json`, `routes.json`, `source_manifest.json`, `integrity.json`, `coverage.json`, `summary.json`, and an empty `correction_ledger.json`. Diagnostics include document-internal heading-hierarchy consistency and a conservative layout-aware reading-order check; deep level-6 headings are not treated as errors merely because of their depth. Validation coverage is explicit, so zero anomalies with poor checker coverage is reported as `limited`/`not_evaluable` rather than clean. ZIP CRC and referenced-artifact integrity are checked before visual routing. Pi5 and OnePlus endpoint health is displayed, while external inference remains disabled by default until routing has been validated. See `docs/stage2-quality-router.md` and `docs/validation-coverage.md`.


### Human-readable quality labels

Stage 2A keeps machine status codes stable for routing and APIs, while adding `display_label` fields for the Quality dashboard and generated reports. For example, `limited` remains the internal value but is displayed as **Not enough checked to be sure**. Human labels are presentation-only and are never sent to Pi5/OnePlus unless explicitly included in a future task prompt.

## GRAB-SMAG regression hardening

This build also includes conservative generic OCR-fragment detection, non-prose reading-order exclusions for form/title/contents/technical-visual pages, and requested-vs-returned watcher format validation. See `docs/grab-smag-regression-fixes.md`.

## Stage 2 rerun and converted-folder import

The Quality & Routing worker no longer depends only on conversion rows already present in `jobs.db`.

- Drop any valid Docling ZIP directly into the configured `output_dir` / `converted/` folder. The worker validates the ZIP, registers it as an **Imported ZIP**, and sends it through Stage 2A automatically.
- Watcher-created ZIPs are never duplicated as imports; an existing watcher job owns its reserved output filename.
- Imported ZIPs do not inflate the main PDF conversion counters/history.
- Completed documents expose **Quality**, **Routing**, and **Rerun** actions in the Quality page. Watcher jobs also expose the Stage 2 actions from the live Documents/Recent documents table once analysis is complete.
- **Rerun** is Stage-2-only. It reuses the existing converted ZIP and regenerates the Stage 2 artifacts with the currently installed code. It does not submit the PDF to Docling again and does not require deleting `jobs.db`.
- Reruns are serialized by the existing Stage 2 worker and are restart-safe. A job already pending/processing cannot be queued twice.
- Unrelated/non-Docling ZIPs in `converted/` are ignored. Their unchanged file signatures are cached so they are not repeatedly re-inspected every poll.

The generated `source_manifest.json` includes `source_kind: "watcher"` or `source_kind: "converted_folder"` so downstream audit logic can identify how the Docling source entered Stage 2.

### Troubleshooting structure detection fix

Stage 2A now recognizes common troubleshooting section-heading variants including `Troubleshooting`, `Troubleshooting Chart`, `Fault Finding`, `Possible/Probable Cause`, `Corrective Action`, `Symptom`, and `Remedy`. Detection stays scoped to Docling section headings to avoid broad false positives from ordinary body prose. The MacGregor CC3000 troubleshooting guide regression now reports `detected_structures.troubleshooting: true`.

## Watcher controls and UI consistency

Overview now contains a dedicated watcher control bar with **Start queued files** and an **Auto Run** switch. Queue ordering is always **smallest first**, and Docling remains serialized to one conversion at a time. The execution controls are deliberately separate from Settings, which is reserved for folders, connection details, and output-format selection.

The UI was also consolidated into one shared component system across Overview, Convert, Quality, and Error Log so new functionality does not keep changing the visual language. See `docs/watcher-execution-ui.md`.

### UI consistency baseline

The frontend now keeps one canonical design-token block, shared action/status primitives, inline operational feedback, and stable user-facing terminology. Internal quality/routing status codes and backend behavior are unchanged.

## Verification execution

The Quality/Verification flow keeps independent Text and Image queues. Each role can use Pi5, OnePlus, or Groq. Requests are serialized by the physical processor, so Pi5 and OnePlus can work concurrently when assigned to different roles, while assigning both roles to the same local device safely serializes them. Results are persisted in SQLite and under each document's `processed/.../verification/` directory. See `docs/stage2b-verification.md`.


## Dedicated verification workspace

Stage 2B verification now lives at `/verification`. It provides per-book manual verification, a master Auto verify all switch, independent Pi5/OnePlus Auto Run controls, and complete remaining queues. Retryable OnePlus/Pi5 failures use per-job exponential backoff so one slow route cannot starve later work. See `docs/stage2b-verification-workspace.md`.

## Stage 2B polling reliability update

The Verification workspace now keeps read-only polling separate from route discovery. GET status/queue/book calls never rescan Stage 2A route files. Route synchronization is coalesced and cached by file signature, frontend polling cannot overlap, Verify book wakes routes currently delayed by retry backoff, and Stage 2B retry/failure details are emitted to container logs. See `docs/stage2b-polling-reliability.md`.

## OnePlus server controller

This build adds a dedicated `/oneplus` page for the Termux vision server. The phone only needs its SSH server running; the web app can Start, Stop, Restart, capture the currently running llama.cpp configuration, discover GGUF models, check `/health`, and view the remote llama-server log.

Before using the controller, copy `.env.example` to `.env` and set `ONEPLUS_SSH_PASSWORD` to the Termux SSH password. The password is never exposed to the browser or stored in `config.yaml`. The controller ships with the proven Qwen3.5 2B Q8 + mmproj launch preset already filled in, including CPU affinity `4,5,6,7`, `nice -n 10`, `-t 4 -tb 4 -c 4096 -np 1`, reasoning disabled, and `--image-max-tokens 1024`. See `docs/oneplus-server-control.md`.


### OnePlus SSH controls

The OnePlus controller page includes **Reconnect SSH** and **Stop SSH**. Stopping SSH does not stop llama-server; it only removes remote control access until `sshd` is started manually in Termux again.


### Stage 2B verifier controls
The Verification workspace has persistent Stop verifier controls for Pi5 and OnePlus and shows completed/failed results only. The OnePlus controller launches llama.cpp detached from SSH and requests a Termux wake lock while the server is running, so page navigation or sshd restarts do not intentionally stop the model server.

## OnePlus phone-script controller

OnePlus llama.cpp lifecycle is deliberately phone-owned. The web app only SSHes
to Termux and invokes `$HOME/bin/oneplus-llama-control` with `start`, `restart`
or `stop`. Use the OnePlus page's **Install / update script** button once after
upgrading. The bundled script contains the validated model command and handles
the Termux wake lock locally on the rooted phone.

## OnePlus bundled control script
The Docker image includes `mobile/oneplus-llama-control` at `/app/mobile/oneplus-llama-control`.
The OnePlus page's **Install / update script** action sends this bundled script over SSH to `$HOME/bin/oneplus-llama-control` on the phone.
Rebuild the image after upgrading this project so the bundled script is present in the container.

## OnePlus script-control packaging + UI fix

- The Docker image now copies `mobile/oneplus-llama-control` to `/app/mobile/oneplus-llama-control`, so **Install / update script** works inside the container.
- The OnePlus page uses the same canonical page header, cards, buttons, badges and sidebar footer as the rest of the app.
- Server commands are a three-column equal-width action row on desktop and stack on small screens.
- SSH actions are a two-column equal-width row on desktop and stack on small screens.
- Legacy model-browser/process-inspection UI classes were removed from the simplified script-only page.

## Stage 2C conservative correction/enrichment

Completed Stage 2B results now feed an immutable-overlay Stage 2C. Pi5 corrections require deterministic garble/evidence/fidelity gates plus re-verification; OnePlus keeps `visible_text` separate from generated object descriptions and uses structurally corroborated diagram categories. Accepted entries are written to `correction_ledger.json` and `chunk_overlays.jsonl`; the raw Docling ZIP is never modified. See `docs/stage2c-correction-enrichment.md`.

## Stage 2C empirical calibration

Real-manual validation changed two initial conservative heuristics:

- `stage2c_correction_min_garble_score` defaults to `0.12` (legacy `0.30` auto-migrates in memory). Safety still comes from the upstream corrupt/evidence/confidence gates plus correction fidelity and re-verification.
- Vision diagram corroboration uses thin-stroke erosion, filled-core survival and spatial spread instead of row/column ink occupancy, preventing bold logos/wordmarks from acting as schematic evidence.
- Pi5 evidence is bounded to one exact <=120-character suspect-text span; the old 160-token Pi5 budget migrates to 220 to prevent observed JSON truncation without encouraging verbose output.
- Legacy OnePlus `visible_labels` are retained separately as ambiguous observations and are no longer promoted into `visible_text`; legacy full-image summaries are recovered during Stage 2C backfill.
- Vision prompts explicitly classify manual/book covers, title/publisher pages and promotional pages as `cover_art` rather than `technical_photo` merely because technical equipment is pictured.


## Imported converted ZIPs and Stage 2C backfill

ZIPs dropped directly into `converted/` now appear on Overview under **All documents**. After Stage 2B verification has finished, use **Build Stage 2C** to generate the correction/enrichment ledger from the persisted verifier results. This does not rerun Docling or completed OnePlus/Pi5 verification; Pi5 is contacted again only for text items that pass the automatic correction gate.


## Fresh-run crosscheck isolation · version 2026.09.07.5

- Optional OnePlus page-image crosschecks can no longer fail an otherwise completed Pi5 Stage 2B route.
- If llama.cpp truncates after emitting complete `verdict` and `corrected_text` fields, those closed fields are safely recovered and marked `partial_response_recovered`.
- Unrecoverable JSON, phone transport errors, or an offline OnePlus are recorded as an unusable second opinion while the authoritative Pi5 verification still completes.
- The crosscheck prompt now requests exactly `verdict` and `corrected_text` and explicitly forbids verbose `reason`/explanation fields.
- `UNREADABLE` or fidelity-rejected OnePlus alternatives no longer upgrade a rejected Pi5 correction to `proposed`; the original correction status is preserved.

## Guided frontend · version 2026.09.07.5

The home page is now **My books**. Each book shows extraction checks (2A), device
verification (2B), and results (2C), with a recommended next action. Search and
attention filters help find books; readable reports show corrections and image
descriptions without requiring JSON inspection. Conversion controls are at `/queue`.

The navigation footer displays the frontend version and whether it matches
`/api/version` on the running server. A mismatch asks you to refresh. HTML is
revalidated and assets use versioned URLs. When releasing frontend changes,
update `app/version.py`, the badge in `app/static/nav.js`, and HTML asset versions
together; the test suite checks their consistency. Rebuild and restart the
container to deploy this release.



## Human before/after correction review · version 2026.09.07.5

The correction review page now presents the immutable Docling OCR text beside an editable human-corrected version, shows a live word-level removed/added diff, exposes the AI proposal only as a review aid, and saves accepted text as a human-verified Stage 2C overlay. The ledger retains the before/after text, prior status/reason, action and save timestamp; raw Docling output remains unchanged.

## Raw Docling neighbor review · version 2026.09.07.6

The manual **Review corrupted text** page is now grounded in the immutable Docling JSON rather than a Pi5 before/after presentation. For each text-correction entry it loads the exact Docling `source_index` and shows up to three non-empty text blocks **above**, the **corrupted OCR block**, and up to three text blocks **below**, restricted to the same page and preserved in Docling reading order.

The editable correction field starts from the corrupted Docling text itself; Pi5 suggestions are not prefilled into the manual review. The reviewer can use the original PDF page plus neighboring Docling blocks to reconstruct only the damaged target span, then save it as a `human_verified_manual_correction` overlay. Raw Docling JSON and the converted ZIP remain unchanged.

API: `GET /api/postprocess/jobs/{job_id}/corrections/{entry_id}/docling-context`.

## Stage 3 HybridChunker via existing Docling Serve

Stage 3 now builds `chunks.jsonl` by calling the same `docling_url` used by this project (default `http://192.168.68.63:5001`). It does not install Docling locally in this container and does not reconvert the PDF. Accepted Stage 2C text overlays are applied only to an in-memory copy of the Docling JSON, which is sent as `json_docling` to Docling Serve's HybridChunker endpoint. See `docs/stage3-docling-hybrid-chunking.md`.


## Remote HybridChunker · version 2026.09.07.9

Stage 3 uses the project's existing `docling_url` for HybridChunker. After Stage 2C, accepted text overlays are applied only to an in-memory copy of the Docling JSON and uploaded as `json_docling` to `/v1/chunk/hybrid/file/async`. No PDF/OCR reconversion occurs. The resulting `chunks.jsonl` preserves Docling headings, captions, page numbers and `doc_items`, and adds Stage 2C correction/vision provenance.


## Human correction precedence · version 2026.09.07.9

Human review is authoritative. Once a Stage 2C text-correction entry has `human_verified: true`, later automatic Pi5/Stage 2C writes for the same stable entry ID cannot replace it. Rebuilding Stage 2C therefore preserves human corrections, and rerunning the same verifier route cannot silently restore an automatic proposal over a human-approved value. Only a later human action may update that human-verified entry.

## Stage 3 table-limit + human audit cleanup · version 2026.09.07.9

- Human-reviewed corrections now set both `reason` and `status_reason` to `HUMAN_VERIFIED`; the previous automatic reason/status_reason is retained under `human_review` for audit.
- Stage 3 post-validates HybridChunker output against `stage3_chunk_max_tokens`.
- Oversized canonical Markdown tables are split only at complete row boundaries and repeat the Markdown header/separator in every child chunk.
- Non-table or otherwise unsafe-to-split chunks are never blindly cut; they are retained and explicitly flagged for audit.
- Post-split token counts are conservative estimates derived from the parent Docling tokenizer count and marked with `num_tokens_estimated=true`.
- Real Anemometer replay: 29 remote Docling chunks -> 30 final chunks; 275-token table -> 253 + 165 estimated-token children; 0 remaining over 256.

- Existing human-verified ledger entries from older builds are normalized during Build Stage 2C so stale automatic live status_reason values are cleaned without changing the human text or decision.

## 2026.09.08.12

- Fix Stage 3 terminal-state UI: persisted/in-memory `completed` HybridChunker jobs now render green and expose **Download chunks** + **Rebuild chunks** instead of another Build button.
- Keep Pi5 detection and correction separate: high-confidence `LIKELY_CORRUPT` / `OCR_GARBLE` findings can receive a conservative correction suggestion even below the 0.12 automatic-apply garble gate.
- Automatic Stage 2C application remains unchanged and still requires the strict garble/fidelity/re-verification gates.
- Human Review can optionally show a Pi5 correction candidate with **Use Pi5 suggestion**; the editor still starts from raw Docling text and the human decision remains authoritative.
- Existing completed books can use **Generate missing Pi5 suggestions** without rerunning Docling, Stage 2A, Stage 2B, or OnePlus.

## Long-running Docling conversions (2026.09.08.13)

`document_timeout_minutes` is now a **long-running warning threshold**, not a hard failure cutoff. Large OCR-heavy manuals can legitimately run for several hours on CPU. As long as Docling Serve has accepted the document and the app still has its `docling_task_id`, the worker keeps polling that same remote task and survives app/container restarts without resubmitting the PDF.

Older releases could mark a job `Timeout` after 120 minutes while Docling continued processing. On upgrade, Retry preserves a saved remote task id and resumes polling it instead of submitting a duplicate conversion. Only a terminal failure explicitly reported by Docling clears the remote task id and allows a fresh submission.

Successful result downloads use the separate `docling_result_timeout_seconds` setting (default 600 seconds), so a large returned ZIP is not constrained by the ordinary 60-second API read timeout. The Overview queue shows long-running jobs, elapsed minutes, task IDs, and conversion error details with a direct Error Log link.



## Historical: Pi5 uncertainty recovery (2026.09.08.15, superseded for live text reconstruction)

Pi5 evidence handling is now tolerant of presentation mistakes without becoming fuzzy. Overlong copied evidence, harmless wrappers such as `SUSPECT TEXT: '...'`, and truncated JSON with all required fields already closed can be recovered only when the resulting evidence is still an exact normalized span from the Docling suspect text. Invalid/context-only evidence remains `UNCERTAIN`.

Generic Stage 2A recall candidates no longer lose a source-grounded `LIKELY_CORRUPT` verdict solely because their structural garble score is low; plausible alphabetic OCR errors are expected to look structurally normal. Technical-format protection remains authoritative, and low garble still prevents automatic Stage 2C application.

That release used `LIKELY_OK / LIKELY_CORRUPT / UNCERTAIN` as the live text-verification policy. Version 2026.09.11.25 supersedes that path for new Text routes with direct target-crop source transcription. The legacy verdict/parser code is retained only for old saved-result compatibility and historical re-evaluation. New source-image reconstruction does not require a human gate. Human-verified entries remain authoritative.

## Generic Stage 2A OCR recall (2026.09.08.14)

Stage 2A now has two independent OCR candidate generators:

1. **Structural corruption scan** — replacement/control characters, numeric/unit confusions and fragmented OCR.
2. **Document-internal recall scan** — learns only from the current document and finds rare near-matches to frequent in-document tokens, suspicious split/join forms, spaced apostrophes, and corroborated variants of repeated text blocks.

The recall layer uses **no external spellchecker, no manufacturer vocabulary and no book-specific rules**. It never changes Docling text. It only identifies candidate target blocks. Version 2026.09.11.25 then re-reads those targets from isolated source-image crops with the selected Text processor; the recall heuristic itself is never allowed to invent a correction.

Default controls:

```yaml
stage2a_ocr_recall_enabled: true
stage2a_ocr_recall_max_candidates: 300
stage2a_ocr_recall_common_min_count: 5
stage2a_ocr_recall_rare_max_count: 2
```

The candidate cap bounds Pi5 load on very large manuals. Existing converted ZIPs do not need Docling reconversion; rerun **Stage 2A** to generate the new recall routes. Existing human-verified corrections remain authoritative.

## Selectable Groq/Offline verification + usage audit · version 2026.09.10.18

The Verification page exposes two independent persisted selectors:

- **Text:** Cloud · Groq (`openai/gpt-oss-20b`) or Offline · Pi5.
- **Vision:** Cloud · Groq Vision (`qwen/qwen3.8-27b`) or Offline · OnePlus.

There is **no automatic cloud↔offline fallback**. If Cloud is selected and its request cannot run, the route remains on the selected cloud path until you change the selector or retry later. Manual crossover also respects the selected opposite modality. The API key is read only from the `GROQ_API_KEY` environment variable and is never returned by settings or stored in `config.yaml`.

### Groq usage audit

Every Groq HTTP response made by this app is recorded as metadata in `/data/db/groq_quota.json`. The Verification page shows calls in the last 24 hours, success/failure counts, input/output/total tokens, paid-equivalent estimated cost, per-model/kind totals, and a recent-call table containing timestamp, model, call kind, book/route, HTTP code, token counts, latency, request ID and provider error code. **Prompts, images and API-key values are never stored in the usage ledger.** The ledger starts with calls made by this release; it does not import historical Groq-console traffic.

### Free-tier quota guard

The guard is deliberately conservative and applies to whichever selected modality uses Groq. Defaults warn at 80% and pause new Groq requests at 90% of the configured free daily request/token allowance. The request counter uses both local usage and Groq's RPD response headers. Because Groq token headers represent TPM rather than TPD, the app persists actual response token usage and protects daily usage with a conservative rolling 24-hour counter. It also checks the server-reported TPM remainder before sending another request.

When the safety reserve is reached, the current in-flight request may finish but no new Groq request starts; queued Groq routes stay pending and do not consume retry budget; roles assigned to Pi5/OnePlus and Docling can continue; and the UI shows the pause/resume state. An unexpected HTTP 429 is treated as a quota/rate pause, not an OCR failure.

```yaml
text_verifier_provider: pi5        # pi5 | oneplus | groq
vision_verifier_provider: oneplus  # pi5 | oneplus | groq
text_cloud_model: openai/gpt-oss-20b  # legacy text-only compatibility utilities
vision_cloud_model: qwen/qwen3.8-27b
text_cloud_quota_guard_enabled: true
text_cloud_free_daily_request_limit: 1000
text_cloud_free_daily_token_limit: 200000
text_cloud_quota_warn_fraction: 0.80
text_cloud_quota_stop_fraction: 0.90
text_cloud_quota_state_path: /data/db/groq_quota.json
groq_usage_log_max_entries: 2000
```

The configured free limits are safety defaults, not an entitlement check; update them if the limits shown for your Groq organization differ. For the strongest daily-token protection, dedicate the configured Groq organization/key to this application because token use made by other applications cannot be reconstructed locally.


### Source-image direct text reconstruction (.21)

Direct text reconstruction is now provider-neutral. Pi5, OnePlus, or Groq may be selected for the Text role. The app renders only the Docling target bbox from the original PDF/raster source and asks the selected vision-capable processor for an exact transcription. The response contract is `READABLE`/`UNREADABLE`, not `AGREES`/`DISAGREES`. READABLE text is applied directly when it differs; UNREADABLE preserves immutable Docling. Human-verified corrections remain authoritative and are never overwritten automatically.


## 2026.09.13.28

- Adds scope-inversion rejection and strict table-cell neighbor-contamination protection in Stage 2B, with independent Stage 2C backstop protection (`stage2c-structural-v5`).
- Stage 3 now safely compacts Markdown presentation overhead, splits only at complete logical boundaries, and compacts repeated ancestor-heading text while preserving raw text/provenance.

## 2026.09.12.26

- Adds a deterministic **wrong-region auto-apply guard**. A direct source transcription with zero target-token recall and poor character alignment is `UNRESOLVED`; immutable Docling is preserved instead of replacing the target with a nearby label.
- Adds a **high-risk technical-token alignment guard** for numeric values, units, terminal/component identifiers and similar technical tokens. These may still be corrected, but only when the surrounding target alignment is strong enough to prove localization.
- Adds an independent **Stage 2C fail-safe**. Even if an older/saved Stage 2B result says `applied`, Stage 2C refuses to emit an overlay when deterministic source/target alignment is contradictory. Human-verified decisions remain authoritative.
- `revalidate-saved-pi5` now re-checks completed direct source-image reconstructions without model calls, so existing `.25` results can be demoted safely instead of rerunning hundreds of Pi5 requests.
- Vision results now protect line/diagram-like images from automatic exclusion. `DECORATIVE_OR_LOW_VALUE + diagram_like=true` becomes `UNCERTAIN / VISION_DIAGRAM_CONFLICT_REVIEW` unless the stronger technical-diagram corroboration rule promotes it to `TECHNICAL_USEFUL`. Stage 2C independently enforces the same protection on saved/legacy results.
- `_processed (8).zip` replay: **35 / 441** previously auto-applied text reconstructions are now conservatively blocked, including the observed `Transmilter Terminal -> +1 SW RX NIX TX` and `No.6 Operator Workstatlon -> C 220V` wrong-region cases. **17 / 25** previously excluded Vision images are restored to review because deterministic structure says they are diagram-like. Hydraulics and AD136TI retain **0** newly-blocked text corrections under the new alignment policy.

## 2026.09.11.25

- Adds a dedicated **Vision Verifier Audit** page at `/vision-audit`. It is read-only and never starts/reruns verification.
- Each completed image route shows the exact full source image, every crop actually inspected, Stage 2A route reason, selected provider/model, parsed verdict/confidence/category, visible text, model-described objects, unresolved state, deterministic structural override, raw verifier response, and downstream Stage 2C action.
- Vision result rows on the Verification page now include **Audit classification**, linking directly to the matching audit entry.
- Audit image endpoints reconstruct evidence from the immutable converted Docling ZIP; crop reconstruction uses the crop settings persisted with the original verification request.
- New verification runs persist the exact prompt used for each crop so full-image and crop prompts can both be reviewed later.

## 2026.09.11.24

- Rejects source-image transcriptions that introduce a new adjacent duplicate token (for example `on` -> `on on`) relative to immutable Docling text.
- Keeps the guard non-corrective: original Docling is preserved instead of deleting one duplicate automatically.
- Retains `.23` dynamic 512-1024 direct-transcription token budgets and truncation rejection.


## Release tracking documents

From `.40.3` onward every distributed ZIP includes current project-state documents under `docs/`: tracker, completed implementation, TODO, acquired state, next-phase requirements, new-chat handoff and stage-wise workflow. See `docs/RELEASE_STATE_POLICY.md`.

## v2026.09.20.40.7.1 — Verifier Audit gate + optional Telegram control

`.40.7` adds authoritative human visual decisions in Verifier Audit, a per-book testing bypass that never accepts unresolved evidence, corrected text-audit status wording, and an optional allow-listed Telegram status/control plane. See `docs/RELEASE_VALIDATION_2026.09.20.40.7.1.md`.

## v2026.09.21.40.8A.1 — Canonical OnePlus CPU-isolated llama control

The bundled `mobile/oneplus-llama-control` now uses the user-confirmed canonical OnePlus launch profile: llama.cpp is pinned to CPU cores `4,5,6,7` with `taskset` when available, runs at `nice -n 10`, and uses `-t 4 -tb 4`. Cores `0-3` remain free for Android/Termux/sshd responsiveness. The existing Qwen3.5 2B Q8 model, mmproj, 4096 context, single parallel slot, disabled reasoning, 1024 image-token cap, wake-lock/Doze handling, PID/log lifecycle, and start/restart/stop/status interface are unchanged. No document-processing, retrieval, verification, or web-UI behavior changes in this hotfix.

See `docs/RELEASE_VALIDATION_2026.09.21.40.8A.1.md`.

## v2026.09.21.40.8A — Trusted-decision and correction integrity

`.40.8A` hardens Stage 2A/2B/2C authority and source-fidelity invariants: human-verified ledger entries survive regeneration, manual source-image cross-checks cannot reactivate superseded state or bypass deterministic safety, troubleshooting remedies are compared as counted `(verb, object)` obligations rather than verb types, and table-cell row/column spans are preserved through the verification metadata path. No new dependency is added and raw Docling output remains immutable. See `docs/RELEASE_VALIDATION_2026.09.21.40.8A.md`.

## v2026.09.21.40.8A.2 — Complete Stage 2A route coverage before safety ceiling

Stage 2A no longer stops collecting verification candidates when the route ceiling is reached. It now collects/deduplicates all candidates across all diagnostics, globally sorts them by existing priority/score rules, and only then applies `max_routes_per_document`. The default ceiling is raised from 500 to 5000 and is explicitly a runaway/corruption safety valve, not a normal processing limit. Any candidates beyond the ceiling are retained in `routes.json` as `deferred_routes` with loud summary counts; the normal Stage 2B `pending` queue remains unchanged. Quality/Book UI and Telegram monitoring show queued/total route coverage and deferred counts. The route dedup lookup is also O(1) by key instead of rescanning the growing route list.

See `docs/RELEASE_VALIDATION_2026.09.21.40.8A.2.md`.

## v2026.09.21.40.8B — Safety/concurrency closure + Book Flow audit bypass

`.40.8B` closes the selected September 21 safety/concurrency findings without changing the trusted-decision and route-coverage architecture from `.40.8A.2`: Git publishing no longer deletes history or force-pushes and now refuses staged/tracked secret/runtime paths; verifier transport outages propagate to the existing circuit breaker instead of becoming fake completed evidence; correction rerun generations reconcile to one current automatic entry while human decisions remain authoritative; duplicate document discovery is serialized transactionally; manual cross-check ledger writes use the shared Stage 2C lock; and Groq uses in-flight quota reservations. The reported SQLite-over-SMB finding is intentionally omitted because it came from the review/share environment, not the real container deployment.

The Book Flow page now shows **Bypass audit for testing** for every book. The control remains visible but disabled until required verification is complete, requires confirmation, never accepts unresolved evidence, and can be removed to re-enforce the audit gate.

Validation: **463/463 tests pass** before packaging; the release ZIP is separately revalidated. See `docs/RELEASE_VALIDATION_2026.09.21.40.8B.md`.
