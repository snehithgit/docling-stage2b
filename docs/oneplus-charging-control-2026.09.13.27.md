# OnePlus charging control · 2026.09.13.27

The OnePlus control page now manages both the phone-side llama.cpp server and a rooted OPlus charging controller.

## Phone-side service

Bundled file: `mobile/oneplus-charge-control.sh`

The web installer copies it through Termux SSH into:

`/data/adb/service.d/oneplus-charge-control.sh`

It uses:

- `/sys/class/oplus_chg/battery/mmi_charging_enable`
- `/sys/class/power_supply/battery/capacity`
- `/sys/class/power_supply/battery/status`

Defaults are `LOW=20`, `HIGH=80`, `INTERVAL=30` seconds. In Auto mode it uses hysteresis: charge ON at/below LOW, charge OFF at/above HIGH, and retains the previous desired state between thresholds.

Manual mode is persistent. `manual-on` and `manual-off` immediately write the kernel charging node. Turning Auto off uses `manual-hold`, which preserves the current charging state rather than unexpectedly toggling it.

The controller has a PID file, state file, persistent configuration and log under `/data/adb` / `/data/local/tmp`. Magisk starts it after boot from `service.d`.

## Web controls

Open `/oneplus` and use **Charging control**:

- battery percentage and Android battery status
- actual OPlus charging-node ON/OFF state
- Auto / Manual mode
- controller running/stopped state and PID
- Auto charging ON/OFF
- Manual Start charging / Stop charging
- configurable low/high thresholds and check interval
- Install/update charging controller
- Start/Stop controller service
- last charging-controller log lines

Manual charging buttons are disabled while Auto mode is on. Turn Auto off first; the controller enters Manual mode while holding the current charging state.

## API

- `GET /api/oneplus-control/charge/status`
- `POST /api/oneplus-control/charge/install`
- `POST /api/oneplus-control/charge/auto` with `{ "enabled": true|false }`
- `POST /api/oneplus-control/charge/manual` with `{ "enabled": true|false }`
- `POST /api/oneplus-control/charge/thresholds` with `{ "low": 20, "high": 80, "interval": 30 }`
- `POST /api/oneplus-control/charge/controller` with `{ "enabled": true|false }`

The web container never writes `/sys` directly. Root operations remain on the phone and are invoked through `su -c` over the already configured Termux SSH connection.
