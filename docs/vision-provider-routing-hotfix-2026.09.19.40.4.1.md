# Vision Provider Routing Hotfix — 2026.09.19.40.4.1

## Problem

The Verification page allowed `Pi5 Vision`, `OnePlus Vision`, or `Groq Vision`, but the full technical-artifact sweep still used legacy round-robin assignment. Sweep rows stored a physical `source.processor` (`pi5` or `oneplus`), and Stage 2B treated that value as a forced provider override. Therefore selecting Pi5 Vision could still send some sweep work to OnePlus.

## Fix

1. The selected `vision_verifier_provider` is authoritative for all Vision-role inference.
2. `_vision_client_for_role()` ignores legacy artifact-sweep `source.processor` hints.
3. New `FULL_TECHNICAL_VISUAL` jobs use the historical `oneplus` DB target only as the **Vision role key**; it no longer means the physical OnePlus device.
4. Existing legacy Pi5-lane sweep jobs remain runnable for upgrade compatibility, but their inference client resolves to the currently selected Vision provider.
5. Physical-provider locks remain keyed by `pi5`, `oneplus`, and `groq`, so legacy rows targeting the same selected physical provider cannot run concurrent inference against that device.
6. Artifact Audit UI no longer claims Pi5/OnePlus round-robin load sharing.

## Expected behavior

- Vision = Pi5 → all Vision jobs call `pi5_url`.
- Vision = OnePlus → all Vision jobs call `oneplus_url`.
- Vision = Groq → all Vision jobs call the configured Groq Vision model.
- No automatic fallback.
- No reconversion, Stage 2A rerun, or chunk rebuild is required for this routing-only hotfix.
