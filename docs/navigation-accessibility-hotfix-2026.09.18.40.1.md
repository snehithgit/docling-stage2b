# Navigation/accessibility hotfix — 2026.09.18.40.1

This hotfix changes UI/navigation behavior only. It does not change Docling conversion, Stage 2A/2B/2C, Stage 3 chunk content, BGE vectors, hybrid ranking, or raw source data.

## Fixed

1. Ready books expose both **Open** (`/book?job=<id>`) and **Test RAG** (`/retrieval?job=<id>`).
2. RAG quality reads `?job=<id>` once and selects that book scope automatically.
3. Source-page modal moves keyboard focus inside, traps Tab, and returns focus to the opener when closed.
4. Mobile navigation drawer moves focus inside, traps Tab, closes on Escape/backdrop, and returns focus to the hamburger trigger.
5. Missing book-id error provides a clickable return-to-books link.
6. Every static page has a keyboard-visible skip link to `#main-content`.

The asset version is `.40.1`, forcing browsers to fetch the corrected JavaScript/CSS rather than using cached `.40` assets.
