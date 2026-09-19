#!/system/bin/sh

# Root charging controller for rooted OnePlus/OPlus devices.
# Installed into /data/adb/service.d so Magisk starts it at boot.

VERSION="2026.09.13.28"
NODE="/sys/class/oplus_chg/battery/mmi_charging_enable"
BAT="/sys/class/power_supply/battery/capacity"
ANDROID_STATUS="/sys/class/power_supply/battery/status"
BASE="/data/adb"
CONFIG="$BASE/oneplus-charge-control.conf"
STATE="$BASE/oneplus-charge-state"
PIDFILE="$BASE/oneplus-charge-control.pid"
LOG="/data/local/tmp/oneplus-charge.log"

DEFAULT_LOW=20
DEFAULT_HIGH=80
DEFAULT_INTERVAL=30

log_msg() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
}

int_or_default() {
    value="$1"
    fallback="$2"
    case "$value" in
        ''|*[!0-9]*) echo "$fallback" ;;
        *) echo "$value" ;;
    esac
}

read_cfg_value() {
    key="$1"
    fallback="$2"
    value=""
    if [ -f "$CONFIG" ]; then
        value="$(grep "^${key}=" "$CONFIG" 2>/dev/null | tail -n 1 | cut -d= -f2-)"
    fi
    [ -n "$value" ] && echo "$value" || echo "$fallback"
}

load_config() {
    MODE="$(read_cfg_value MODE auto)"
    LOW="$(int_or_default "$(read_cfg_value LOW "$DEFAULT_LOW")" "$DEFAULT_LOW")"
    HIGH="$(int_or_default "$(read_cfg_value HIGH "$DEFAULT_HIGH")" "$DEFAULT_HIGH")"
    INTERVAL="$(int_or_default "$(read_cfg_value INTERVAL "$DEFAULT_INTERVAL")" "$DEFAULT_INTERVAL")"
    MANUAL="$(read_cfg_value MANUAL 1)"

    [ "$MODE" = "manual" ] || MODE="auto"
    [ "$MANUAL" = "0" ] || MANUAL="1"
    [ "$LOW" -ge 1 ] 2>/dev/null || LOW="$DEFAULT_LOW"
    [ "$HIGH" -le 100 ] 2>/dev/null || HIGH="$DEFAULT_HIGH"
    [ "$HIGH" -gt "$LOW" ] 2>/dev/null || { LOW="$DEFAULT_LOW"; HIGH="$DEFAULT_HIGH"; }
    [ "$INTERVAL" -ge 5 ] 2>/dev/null || INTERVAL="$DEFAULT_INTERVAL"
}

write_config() {
    tmp="${CONFIG}.tmp.$$"
    cat > "$tmp" <<CFG
MODE=$MODE
LOW=$LOW
HIGH=$HIGH
INTERVAL=$INTERVAL
MANUAL=$MANUAL
CFG
    chmod 600 "$tmp" 2>/dev/null || true
    mv "$tmp" "$CONFIG"
}

ensure_config() {
    if [ ! -f "$CONFIG" ]; then
        MODE=auto
        LOW="$DEFAULT_LOW"
        HIGH="$DEFAULT_HIGH"
        INTERVAL="$DEFAULT_INTERVAL"
        MANUAL=1
        write_config
    fi
}

node_ready() {
    [ -e "$NODE" ] && [ -e "$BAT" ]
}

wait_for_driver() {
    while ! node_ready; do
        sleep 5
    done
}

pid_live() {
    [ -f "$PIDFILE" ] || return 1
    pid="$(cat "$PIDFILE" 2>/dev/null)"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

apply_once() {
    ensure_config
    load_config
    node_ready || return 2

    LEVEL="$(cat "$BAT" 2>/dev/null)"
    case "$LEVEL" in ''|*[!0-9]*) return 3 ;; esac

    CURRENT="$(cat "$NODE" 2>/dev/null)"
    [ "$CURRENT" = "0" ] || CURRENT=1

    if [ "$MODE" = "manual" ]; then
        DESIRED="$MANUAL"
    else
        if [ "$LEVEL" -ge "$HIGH" ]; then
            DESIRED=0
        elif [ "$LEVEL" -le "$LOW" ]; then
            DESIRED=1
        elif [ -f "$STATE" ]; then
            DESIRED="$(cat "$STATE" 2>/dev/null)"
            [ "$DESIRED" = "0" ] || DESIRED=1
        else
            DESIRED="$CURRENT"
        fi
    fi

    if [ "$CURRENT" != "$DESIRED" ]; then
        echo "$DESIRED" > "$NODE"
        if [ "$DESIRED" = "1" ]; then
            log_msg "${LEVEL}% -> CHARGING ON mode=$MODE low=$LOW high=$HIGH"
        else
            log_msg "${LEVEL}% -> CHARGING OFF mode=$MODE low=$LOW high=$HIGH"
        fi
    fi
    echo "$DESIRED" > "$STATE"
    return 0
}

release_charging() {
    reason="${1:-controller released}"
    if [ -e "$NODE" ]; then
        current="$(cat "$NODE" 2>/dev/null)"
        if [ "$current" != "1" ]; then
            echo 1 > "$NODE" 2>/dev/null || true
        fi
        echo 1 > "$STATE" 2>/dev/null || true
        log_msg "$reason -> CHARGING ENABLED (fail-open release)"
    fi
}

daemon_cleanup() {
    rm -f "$PIDFILE"
    release_charging "controller exit"
}

daemon_loop() {
    echo "$$" > "$PIDFILE"
    trap 'exit 0' TERM INT
    trap 'daemon_cleanup' EXIT
    wait_for_driver
    ensure_config
    log_msg "controller started pid=$$ version=$VERSION"
    while true; do
        apply_once || true
        load_config
        sleep "$INTERVAL"
    done
}

start_controller() {
    if pid_live; then
        echo "ALREADY_RUNNING pid=$(cat "$PIDFILE")"
        return 0
    fi
    rm -f "$PIDFILE"
    ensure_config
    nohup "$0" daemon >/dev/null 2>&1 </dev/null &
    newpid=$!
    sleep 1
    if pid_live; then
        echo "STARTED pid=$(cat "$PIDFILE")"
        return 0
    fi
    echo "ERROR controller failed to start (launcher pid=$newpid)" >&2
    return 20
}

stop_controller() {
    if pid_live; then
        pid="$(cat "$PIDFILE")"
        kill -TERM "$pid" 2>/dev/null || true
        count=0
        while kill -0 "$pid" 2>/dev/null && [ "$count" -lt 10 ]; do
            sleep 1
            count=$((count + 1))
        done
        kill -KILL "$pid" 2>/dev/null || true
        rm -f "$PIDFILE"
        log_msg "controller stopped pid=$pid"
        echo "STOPPED pid=$pid"
    else
        rm -f "$PIDFILE"
        echo "STOPPED no-running-controller"
    fi
    release_charging "controller stopped"
}

set_auto() {
    ensure_config
    load_config
    MODE=auto
    write_config
    apply_once || true
    echo "MODE=auto"
}

set_manual_hold() {
    # Backward-compatible name. Since .28, leaving Auto mode fails open:
    # manual mode is entered with charging enabled so a previous high-threshold
    # OFF state cannot strand the phone unable to charge.
    ensure_config
    load_config
    MODE=manual
    MANUAL=1
    write_config
    release_charging "auto disabled"
    echo "MODE=manual CHARGING=on RELEASED=1"
}

set_release() {
    set_manual_hold
}

set_manual() {
    desired="$1"
    [ "$desired" = "0" ] || desired=1
    ensure_config
    load_config
    MODE=manual
    MANUAL="$desired"
    write_config
    apply_once || true
    if [ "$desired" = "1" ]; then
        echo "MODE=manual CHARGING=on"
    else
        echo "MODE=manual CHARGING=off"
    fi
}

set_thresholds() {
    low="$(int_or_default "$1" -1)"
    high="$(int_or_default "$2" -1)"
    interval="$(int_or_default "${3:-$DEFAULT_INTERVAL}" "$DEFAULT_INTERVAL")"
    if [ "$low" -lt 1 ] 2>/dev/null || [ "$high" -gt 100 ] 2>/dev/null || [ "$high" -le "$low" ] 2>/dev/null; then
        echo "ERROR thresholds require 1 <= low < high <= 100" >&2
        return 30
    fi
    [ "$interval" -ge 5 ] 2>/dev/null || interval="$DEFAULT_INTERVAL"
    ensure_config
    load_config
    LOW="$low"
    HIGH="$high"
    INTERVAL="$interval"
    write_config
    apply_once || true
    echo "THRESHOLDS low=$LOW high=$HIGH interval=$INTERVAL"
}

status_controller() {
    ensure_config
    load_config
    level=""
    enabled=""
    android=""
    state=""
    [ -e "$BAT" ] && level="$(cat "$BAT" 2>/dev/null)"
    [ -e "$NODE" ] && enabled="$(cat "$NODE" 2>/dev/null)"
    [ -e "$ANDROID_STATUS" ] && android="$(cat "$ANDROID_STATUS" 2>/dev/null)"
    [ -f "$STATE" ] && state="$(cat "$STATE" 2>/dev/null)"
    running=0
    pid=""
    if pid_live; then
        running=1
        pid="$(cat "$PIDFILE" 2>/dev/null)"
    fi
    echo "VERSION=$VERSION"
    echo "NODE_PRESENT=$([ -e "$NODE" ] && echo 1 || echo 0)"
    echo "BATTERY_PRESENT=$([ -e "$BAT" ] && echo 1 || echo 0)"
    echo "BATTERY=$level"
    echo "ANDROID_STATUS=$android"
    echo "CHARGING_ENABLED=$enabled"
    echo "MODE=$MODE"
    echo "LOW=$LOW"
    echo "HIGH=$HIGH"
    echo "INTERVAL=$INTERVAL"
    echo "MANUAL=$MANUAL"
    echo "DESIRED_STATE=$state"
    echo "CONTROLLER_RUNNING=$running"
    echo "PID=$pid"
}

case "${1:-start}" in
    daemon) daemon_loop ;;
    start) start_controller ;;
    stop) stop_controller ;;
    restart) stop_controller >/dev/null 2>&1 || true; start_controller ;;
    status) status_controller ;;
    auto) set_auto ;;
    manual-hold) set_manual_hold ;;
    release) set_release ;;
    manual-on) set_manual 1 ;;
    manual-off) set_manual 0 ;;
    thresholds) set_thresholds "${2:-}" "${3:-}" "${4:-$DEFAULT_INTERVAL}" ;;
    apply) apply_once; status_controller ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|auto|manual-hold|release|manual-on|manual-off|thresholds LOW HIGH [INTERVAL]|apply}" >&2
        exit 2
        ;;
esac
