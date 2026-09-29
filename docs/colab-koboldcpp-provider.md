# Optional Google Colab Qwen3-VL / KoboldCpp verifier

Marine Pipeline Studio remains fully usable with the local N150 + Pi5 + OnePlus stack. Colab is an **optional explicit provider** and is never an automatic fallback.

## Colab notebook

Use `tools/koboldcpp_qwen3vl_colab_secured.ipynb` with a Colab GPU runtime. The notebook:

- downloads KoboldCpp and Qwen3-VL-8B-Instruct GGUF + mmproj;
- starts KoboldCpp with CUDA offload;
- creates a random per-runtime API key;
- supplies the key to KoboldCpp through `KCPP_PASSWORD` rather than exposing it in the process command;
- creates a `trycloudflare.com` tunnel;
- prints the tunnel root URL, OpenAI `/v1` base URL, and API key;
- performs authenticated text and vision smoke tests.

The tunnel address is public. Keep the API key private and do not share/save notebook output containing it.

## Configure Marine Pipeline Studio

Open **Verification -> Google Colab · KoboldCpp** and enter:

- current tunnel URL, either the root URL or the `/v1` URL;
- model name (`koboldcpp` for the supplied notebook);
- API key printed by the notebook;
- Enable Colab provider;
- optionally enable **Artifact idle pool**.

Press **Save**, then **Test connection**. The test performs an authenticated generation request, not only a public health/model lookup.

The key is not written to `config.yaml`. It is stored separately at `colab_api_key_path` (default `/data/db/colab_koboldcpp.key`) with mode `0600`, unless `COLAB_KCPP_API_KEY` is supplied in the environment. Public status/settings APIs expose only whether a key is configured.

## Provider behavior

Text and Vision provider selection is explicit. Choosing Colab for Text sends the same source-PDF/crop reconstruction requests to Qwen3-VL. Choosing Colab for Vision also routes durable Human Visual Evidence Recovery jobs through Colab. No job silently falls back to Colab or away from it.

When **Artifact idle pool** is enabled, Colab becomes a third work-stealing Artifact worker. The scheduler-level provider reservation ensures only one Text/Vision/Artifact request owns the Colab model at a time. Normal Text/Vision work has priority over Artifact work.

The shared **Start / Stop / Auto** interlock remains authoritative:

- Stop: no new Text/Vision/Artifact dispatch, including Colab;
- Start: authorizes the current normal queue snapshot; eligible artifacts can follow;
- Auto: continuously dispatches normal work first and lets idle workers drain eligible artifacts.

## Tunnel loss / authentication failure

Colab is ephemeral. If the tunnel disappears or the key is rejected, the affected Stage 2B row is deferred rather than discarded. The Colab circuit breaker prevents tight retry loops. Circuit recovery validates the saved API key against a protected generation endpoint before reopening dispatch.

There is no automatic provider fallback. Restart the notebook, paste the new URL/key, test it, or explicitly switch the role back to Pi5/OnePlus/Groq.

## Data boundary

Selecting Colab means the selected text crop/image/artifact is sent over the network to the Colab/KoboldCpp worker. Leave the provider on Pi5/OnePlus when a book must remain local-only.
