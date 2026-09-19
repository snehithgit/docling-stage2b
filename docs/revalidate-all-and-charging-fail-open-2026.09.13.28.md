# All-books safety refresh + charging fail-open · 2026.09.13.28

## One-click document maintenance

Verification now exposes **Revalidate all + rebuild**. The background workflow processes eligible books sequentially:

1. Revalidate saved Text-verifier/Pi5 results with the current deterministic safety policy.
2. Rebuild Stage 2C from persisted Stage 2B results.
3. Rebuild Stage 3 HybridChunker output.

No Pi5, OnePlus, or Groq verification request is made by this maintenance action. A book with pending, processing, or failed Stage 2B routes is skipped and reported instead of being partially rebuilt.

## Charging controller fail-open behavior

Disabling Auto charging now enters manual mode with charging enabled. Stopping or exiting the root controller also writes `1` to `/sys/class/oplus_chg/battery/mmi_charging_enable` and records the released state. This prevents a previous high-threshold OFF state from leaving the phone unable to charge when the controller is no longer active.

Manual **Stop charging** remains an explicit override and intentionally keeps charging disabled until the user presses **Start charging** or enables Auto charging again.
