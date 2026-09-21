# Full technical artifact sweep — 2026.09.16.37

## Scope

The artifact audit can now launch a fresh full vision pass over every Docling picture in the Stage 2A technical-visual universe. The classification set is intentionally the same generic set already used by Stage 2A: `engineering_drawing`, `flow_chart`, `screenshot_from_manual`, `table`, `line_chart`, `bar_chart`, `box_plot`, `full_page_image`, and `geographical_map`.

On the supplied seven-book processed dataset, Stage 2A reports 2,760 pictures and 1,395 technical visuals. The new sweep targets all 1,395, not only the older 157 low-confidence routes.

## Load sharing

Fresh artifact routes use stable IDs such as `AV000123`. A deterministic parity assignment splits them between Pi5 and OnePlus, so the same artifact always returns to the same worker when the sweep is resumed. Both existing Stage 2B device loops can therefore process picture jobs concurrently while preserving one inference at a time per physical device.

## Audit behavior

Artifact audit and Vision audit accept picture jobs from either local worker. Pi5 picture jobs are kept out of Text audit. Stage 2C records picture results as `vision_enrichment` regardless of which local worker processed the image.

## Safety

The source Docling ZIP is read-only. Full-sweep jobs are additive and do not supersede previous Stage 2B rows. Repeated preparation is idempotent. Failed-artifact retry is restricted to `FULL_TECHNICAL_VISUAL` jobs.
