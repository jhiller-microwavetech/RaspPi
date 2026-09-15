#!/usr/bin/env bash
# Installs the udev rule and a cron @reboot entry for pt_thermal_viewer
# so it starts automatically on every boot.
#
# Run on the Raspberry Pi itself (not the dev machine), from anywhere:
#   sudo bash packaging/install.sh
#
# Safe to re-run: every step below is idempotent.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "This script needs root (it writes to /etc and edits pi's crontab). Re-run with sudo." >&2
    exit 1
fi

# Resolve paths relative to this script's location, so it works regardless
# of where the repo was cloned/copied to.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(dirname -- "$SCRIPT_DIR")"          # .../pt_thermal_viewer
UDEV_SRC="$SCRIPT_DIR/99-purethermal.rules"
KIOSK_SCRIPT="$SCRIPT_DIR/run_kiosk.sh"
EXPECTED_MAIN="/home/pi/Downloads/RaspPi/pt_thermal_viewer/src/main.py"
CRON_USER="pi"

echo "== pt_thermal_viewer autostart installer (cron @reboot) =="
echo "Repo root detected as: $REPO_ROOT"

# --- Sanity check: does the install location match what run_kiosk.sh expects? ---
if [[ ! -f "$EXPECTED_MAIN" ]]; then
    echo
    echo "WARNING: $EXPECTED_MAIN does not exist." >&2
    echo "packaging/run_kiosk.sh hardcodes that path (APP_DIR/XAUTHORITY)." >&2
    echo "If this repo lives somewhere else, edit packaging/run_kiosk.sh" >&2
    echo "(APP_DIR, XAUTHORITY) to match before continuing, then re-run." >&2
    read -rp "Continue installing anyway? [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]] || exit 1
fi

# --- Sanity check: is a PureThermal board plugged in, and does its VID:PID
#     match what the udev rule and README assume (1e4e:0100)? ---
if command -v lsusb &>/dev/null; then
    if lsusb | grep -qi "1e4e:0100"; then
        echo "Found PureThermal board at USB ID 1e4e:0100 (matches udev rule)."
    elif lsusb | grep -qi "1e4e:"; then
        FOUND_ID="$(lsusb | grep -i '1e4e:' | grep -oE '[0-9a-fA-F]{4}:[0-9a-fA-F]{4}' | head -1)"
        echo
        echo "WARNING: found a GroupGets/FLIR device at $FOUND_ID, but the udev" >&2
        echo "rule and README assume 1e4e:0100. Edit packaging/99-purethermal.rules" >&2
        echo "to match $FOUND_ID before (or after) running this script, then:" >&2
        echo "  sudo udevadm control --reload-rules && sudo udevadm trigger" >&2
    else
        echo
        echo "NOTE: no PureThermal board currently detected on USB. That's fine" \
             "if it's not plugged in yet -- just double-check the VID:PID in" \
             "packaging/99-purethermal.rules against 'lsusb' once it is."
    fi
else
    echo "NOTE: lsusb not found, skipping USB device check."
fi

# --- udev rule ---
echo
echo "Installing udev rule..."
install -m 0644 "$UDEV_SRC" /etc/udev/rules.d/99-purethermal.rules
udevadm control --reload-rules
udevadm trigger
echo "  -> /etc/udev/rules.d/99-purethermal.rules installed and reloaded."

# --- cron @reboot entry ---
echo
echo "Installing cron @reboot entry for user '$CRON_USER'..."
chmod +x "$KIOSK_SCRIPT"

CRON_LINE="@reboot $KIOSK_SCRIPT"
EXISTING="$(crontab -u "$CRON_USER" -l 2>/dev/null || true)"

if grep -qF "$KIOSK_SCRIPT" <<<"$EXISTING"; then
    echo "  -> cron already has an entry for run_kiosk.sh, leaving it as-is."
else
    { printf '%s\n' "$EXISTING"; echo "$CRON_LINE"; } | crontab -u "$CRON_USER" -
    echo "  -> added: $CRON_LINE"
fi

echo
echo "== Done. Before rebooting, double check: =="
echo "  1. raspi-config -> System Options -> Boot / Auto Login -> Desktop Autologin"
echo "  2. raspi-config -> Advanced Options -> Wayland -> X11"
echo "     (Bookworm and later default to Wayland/labwc on every Pi model;"
echo "      run_kiosk.sh's DISPLAY=:0 / XAUTHORITY assume the classic X11 desktop."
echo "      See AUTOSTART.md if you'd rather stay on Wayland/labwc.)"
echo "  3. The PureThermal board has already been configured once via GetThermal"
echo "     (Raw14 + TLinear + telemetry footer) -- see the main README."
echo
echo "Test now without rebooting:"
echo "  sudo -u $CRON_USER $KIOSK_SCRIPT &"
echo "  tail -f /home/pi/Downloads/RaspPi/pt_thermal_viewer/kiosk.log"
