#!/usr/bin/env bash
# Kiosk launch wrapper for pt_thermal_viewer, meant to be run from the
# pi user's crontab via @reboot (see packaging/install.sh).
#
# cron's @reboot fires as soon as cron itself starts, which is normally
# well before the X11 desktop (raspi-config Desktop Autologin) is up, and
# a plain crontab entry has no restart-on-crash the way systemd does. This
# script covers both: it polls for the X server before launching, then
# loops main.py so a crash reopens it instead of leaving a blank screen
# until the next reboot.

APP_DIR="/home/pi/pt_thermal_viewer/src"
LOG="/home/pi/pt_thermal_viewer/kiosk.log"
export DISPLAY=:0
export XAUTHORITY=/home/pi/.Xauthority

# Send all output (ours and main.py's) to the log file from here on.
exec >>"$LOG" 2>&1

echo "=== kiosk wrapper started $(date) ==="

# Wait for the X11 desktop session to be up (see AUTOSTART.md for why
# this needs to be X11, not the Wayland/labwc default on current
# Raspberry Pi OS). Poll rather than a fixed sleep, since boot time
# varies with SD card speed, and give up after 60s so this doesn't hang
# forever if autologin/X11 isn't actually configured.
if command -v xset &>/dev/null; then
    ready=0
    for i in $(seq 1 60); do
        if xset -display "$DISPLAY" q &>/dev/null; then
            echo "X server ready after ${i}s"
            ready=1
            break
        fi
        sleep 1
    done
    if [[ "$ready" -ne 1 ]]; then
        echo "X server never became ready after 60s -- giving up."
        echo "Check: raspi-config Desktop Autologin + X11 (not Wayland/labwc)."
        exit 1
    fi
else
    echo "xset not found (install x11-xserver-utils); falling back to a" \
         "fixed 15s sleep instead of polling for the X server."
    sleep 15
fi

cd "$APP_DIR" || { echo "cannot cd to $APP_DIR"; exit 1; }

while true; do
    echo "--- launching main.py: $(date) ---"
    /usr/bin/python3 main.py
    status=$?
    echo "--- main.py exited (code $status): $(date), restarting in 3s ---"
    sleep 3
done
