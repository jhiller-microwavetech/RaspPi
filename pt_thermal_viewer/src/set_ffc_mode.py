"""
Standalone test/utility for SYS_FFC_SHUTTER_MODE_OBJ -- run this directly
from a terminal on the Pi (like diagnose_ffc.py) so any failure prints a
full Python traceback. See ffc_control.py's "SYS FFC Shutter Mode"
docstring section before using this -- the byte layout this relies on is
less solid ground than RAD_RUN_FFC was, and this script exists precisely
to let you verify it against real hardware before trusting it at a demo.

Usage (from pt_thermal_viewer/src), run with main.py NOT also running
against the same board (close it first, same reasoning as diagnose_ffc.py):

    python3 set_ffc_mode.py                # just read and print the current mode
    python3 set_ffc_mode.py external        # switch to External (no shutter, command-only)
    python3 set_ffc_mode.py manual          # switch to Manual (command-only, still uses shutter)
    python3 set_ffc_mode.py auto            # switch back to Automatic (the normal default)
    python3 set_ffc_mode.py external /dev/video2   # explicit device path

After switching, re-run with no argument a few times over a couple of
minutes to confirm the mode actually stuck (not just that the write
didn't raise) and, for external/manual, that it doesn't drift back to
Auto on its own.
"""
import sys

import ffc_control
from capture import find_purethermal_device

MODE_NAMES = {
    "manual": ffc_control.FFCShutterMode.MANUAL,
    "auto": ffc_control.FFCShutterMode.AUTO,
    "external": ffc_control.FFCShutterMode.EXTERNAL,
}

args = [a for a in sys.argv[1:]]
mode_arg = None
device_arg = None
for a in args:
    if a.lower() in MODE_NAMES:
        mode_arg = a.lower()
    else:
        device_arg = a

device = device_arg if device_arg else find_purethermal_device()
if device is None:
    print("Could not auto-detect the device -- pass it explicitly, e.g.:")
    print("    python3 set_ffc_mode.py external /dev/video2")
    sys.exit(1)

print(f"Using device: {device}")

print("\n--- Reading current SYS_FFC_SHUTTER_MODE_OBJ ---")
current_mode, raw = ffc_control.get_ffc_shutter_mode(device)
print(f"Raw bytes: {raw.hex()}")
print(f"Decoded mode: {current_mode.name} ({int(current_mode)})")

if mode_arg is None:
    print("\nNo mode argument given -- read-only, nothing changed.")
    print("Pass manual/auto/external as an argument to change it.")
    sys.exit(0)

target_mode = MODE_NAMES[mode_arg]
if target_mode == current_mode:
    print(f"\nAlready in {target_mode.name} -- nothing to do.")
    sys.exit(0)

print(f"\n--- Setting mode to {target_mode.name} ---")
readback_mode = ffc_control.set_ffc_shutter_mode(device, target_mode)
if readback_mode == target_mode:
    print(f"Confirmed: mode is now {readback_mode.name}.")
    if target_mode == ffc_control.FFCShutterMode.EXTERNAL:
        print(
            "\nNext: hold a uniform piece of metal in front of the lens, "
            "then trigger FFC (the app's Run FFC button, or "
            "`python3 -c \"import ffc_control; "
            f"ffc_control.trigger_ffc('{device}')\"`), then remove the "
            "metal. Re-run this script with no args a couple minutes "
            "later to confirm it hasn't drifted back to Auto on its own."
        )
else:
    print(
        f"WARNING: asked for {target_mode.name} but read back "
        f"{readback_mode.name} -- the write may not have taken, or the "
        f"byte-order detection guessed wrong. Don't trust this mode is "
        f"actually set; fall back to just using Run FFC with the metal "
        f"plate manually before/during the demo instead."
    )
