# OnePlus llama-only control · 2026.09.15.33

The OnePlus integration is intentionally limited to the phone-side llama.cpp server.

## Included

- SSH reachability/status
- bundled `$HOME/bin/oneplus-llama-control` install/update
- llama.cpp status via the phone-side script
- Start
- Restart
- Stop
- last command response

## Removed

- rooted charging controller
- `/data/adb/service.d/oneplus-charge-control.sh`
- `mmi_charging_enable` access
- charging thresholds / auto mode / manual charge on/off
- charging API endpoints and charging UI
- Termux SSH stop/reconnect controls

No document-processing or retrieval logic changes in this release.
