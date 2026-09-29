# Marine Pipeline Studio 2026.09.29.40.11AC — Release validation

## Purpose

Add the user's Google Colab Qwen3-VL / KoboldCpp runtime as an **optional authenticated verifier provider** without weakening the local-first pipeline, durable Stage 2B scheduler, or Text/Vision/Artifact physical-provider interlock.

## Colab provider

40.11AB adds `colab` to the explicit Text and Vision provider choices. The historical logical database lanes remain `pi5` (Text) and `oneplus` (Vision), so no database migration or Stage 2A rerun is required.

When selected:

- Text source reconstruction uses the existing source-image crop pipeline with the Colab VL model.
- Vision verification uses the same evidence prompts/crops with Colab.
- Human Visual Evidence Recovery follows the selected Vision provider and therefore uses Colab when Vision is set to Colab.
- When `colab_artifact_enabled` is true, Colab joins the Artifact shared idle pool as a third worker.

There is **no automatic provider fallback**. Pi5/OnePlus remain the default providers.

## Shared scheduler/interlock

Colab is represented as its own physical provider lock/reservation. Text, Vision and Artifact cannot claim Colab concurrently. Normal Text/Vision routes have priority over Artifact work, including when both logical roles are configured to use Colab.

The existing Start/Stop/Auto interlock remains authoritative. Artifact work on Colab stops when the shared scheduler is stopped and resumes under the same readiness rules.

## Durable outage behavior

The optional remote worker uses the existing endpoint circuit/deferred-job semantics. Tunnel transport errors and authentication/not-found responses do not silently lose the queue row. When a Colab circuit is open, recovery probes require a successful protected generation call before dispatch is reopened, avoiding a public `/v1/models` response falsely validating a bad API key.

## API-key security

A new server-side secret helper stores the Colab key separately from `config.yaml` at `colab_api_key_path` (default `/data/db/colab_koboldcpp.key`) with mode `0600`. `COLAB_KCPP_API_KEY` may override the file. Public settings/status return only `api_key_configured`, never the secret.

The Verification page can save/test the current tunnel URL and key. **Test connection** performs an authenticated `/v1/chat/completions` generation probe.

The supplied `tools/koboldcpp_qwen3vl_colab_secured.ipynb` generates a strong random key per Colab runtime and supplies it to KoboldCpp via the `KCPP_PASSWORD` environment variable, keeping the key out of the process command line. The public Cloudflare tunnel therefore requires the key for generation requests.

## Compatibility

- No Stage 1 rerun required.
- No Stage 2A rerun required.
- No completed local Stage 2B rerun required merely for upgrade.
- Existing Pi5/OnePlus/Groq selections and local-only operation remain unchanged.
- Stage 2C and Stage 3 rule versions are unchanged.
- Raw Docling data and human decisions are unchanged.

## Regression coverage added

- Colab URL normalization accepts either tunnel root or `/v1` base URL.
- Secret-file mode and environment override are tested.
- OpenAI-compatible requests send `Authorization: Bearer <key>` and use the normalized `/v1` endpoints.
- Stage 2B Text/Vision can construct an explicit Colab client with the saved key/model.
- Colab can atomically claim Artifact shared-pool work.
- Existing provider-selector/config/UI tests now include Colab.

## Validation

- working-tree full pytest suite — **624 passed, 1 skipped**;
- Python `compileall` — PASS;
- all frontend JavaScript `node --check` — PASS;
- fresh extraction of the packaged ZIP: **624 passed, 1 skipped**;
- packaged Python `compileall` — PASS;
- packaged frontend JavaScript `node --check` — PASS;
- packaged secured Colab notebook JSON validation — PASS.
