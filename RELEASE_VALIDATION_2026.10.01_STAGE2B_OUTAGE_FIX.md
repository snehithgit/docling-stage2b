# Stage2B outage / queue-stall fix — 2026-10-01

## Source commit

- `8a1ac38f2e4baea827b9489fb594ecfb5639ab4d` — `Fix Stage2B outage queue stall recovery`

## Updated files

- `app/config.py`
- `app/stage2b.py`
- `app/stage2b_store.py`

## Final source blob SHAs

- `app/config.py`: `7172cdc4a7f9dbfc930ce5904132ee27724f3f48`
- `app/stage2b.py`: `3103703b69338904f5d30483c276769b1faa1a1a`
- `app/stage2b_store.py`: `f01bbf302f47707eac224a682414c2ad397475e7`

## Fixes

- Bounds retry-count-exempt endpoint outage deferrals so a permanently unavailable provider cannot stall the queue forever.
- Applies the same bounded outage handling to Colab 401/403/404 tunnel/auth failures.
- Keeps `outage_defer_count` limited to consecutive endpoint outages; quota/cooldown and normal retry/failure/completion/recovery transitions clear it.
- Adds periodic recovery of orphaned `processing` rows in long-lived processes.
- Excludes job IDs still active in the current process from stale-processing recovery to prevent duplicate execution.
- Clears stale claim/outage state when a processing row is reclaimed.
- Adds an additive `outage_defer_count` database migration.

## GitHub validation

The apply workflow verified the exact patch SHA-256, ran `git apply --check`, applied the patch, and successfully ran:

```bash
python -m py_compile app/config.py app/stage2b.py app/stage2b_store.py
```

The temporary patch payload and one-shot apply workflow were removed by the same successful commit.

This validation commit also intentionally triggers the normal Docker publish workflow so `ghcr.io/snehithgit/docling-stage2b:latest` is built from the patched source tree.
