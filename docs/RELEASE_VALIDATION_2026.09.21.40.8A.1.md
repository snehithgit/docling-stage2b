# Marine Pipeline Studio v2026.09.21.40.8A.1

## Scope

Small OnePlus control-script hotfix on top of `.40.8A`. No Stage 2A/2B/2C, Stage 3, retrieval, embedding, RAG, Telegram, or web workflow semantics changed.

## Canonical OnePlus launch profile

`mobile/oneplus-llama-control` now uses the user-confirmed canonical profile:

- model: `Qwen3.5-2B-Qwen3.6-plus-Distilled-q8_0.gguf`
- mmproj: `Qwen3.5-2B-Opus-Distilled-Heretic-Thinking-Multistage-SFT-v1.0.mmproj-q8_0.gguf`
- CPU affinity: cores `4,5,6,7` through `taskset -c` when available
- process priority: `nice -n 10` when available
- generation threads: `-t 4`
- batch threads: `-tb 4`
- context: `-c 4096`
- parallel slots: `-np 1`
- reasoning disabled with zero reasoning budget
- image token cap: `1024`
- bind: `0.0.0.0:8080`
- Termux wake lock plus rooted Doze-whitelist fallback retained
- PID/log lifecycle and `start|restart|stop|status` interface retained

The controller still installs the bundled script to `$HOME/bin/oneplus-llama-control`; no Python-side process-management logic was added.

## Regression coverage

`tests/test_oneplus_control.py` now asserts the CPU-affinity/nice profile and `-tb 4` so future releases cannot silently revert to the prior `-tb 6` launch.

## Validation

Validation is performed from the packaged source before release.
