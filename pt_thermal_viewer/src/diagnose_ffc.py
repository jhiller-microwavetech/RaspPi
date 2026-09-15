"""
Standalone FFC diagnostic -- run this directly from a terminal on the Pi
instead of clicking the button in the app, so any failure prints a full
Python traceback (the app's QMessageBox only shows str(exc), which is
exactly why the last two error reports were ambiguous about which call
actually failed).

Usage (from pt_thermal_viewer/src):
    python3 diagnose_ffc.py                # auto-detect the device
    python3 diagnose_ffc.py /dev/video2    # explicit device path

Run this while main.py is NOT also running against the same board (close
it first) -- we want to isolate whether contention with the app's own
capture thread matters, one variable at a time. If it works here but not
from within the running app, that itself is useful information.
"""
import sys

import ffc_control
from capture import find_purethermal_device

device = sys.argv[1] if len(sys.argv) > 1 else find_purethermal_device()
if device is None:
    print("Could not auto-detect the device -- pass it explicitly, e.g.:")
    print("    python3 diagnose_ffc.py /dev/video2")
    sys.exit(1)

print(f"Using device: {device}")

print("\n--- Step 1: resolve USB bus/address from sysfs ---")
bus, address = ffc_control._usb_bus_address_for_v4l2(device)
print(f"bus={bus} address={address}")

print("\n--- Step 2: open the raw USB device with pyusb (read-only) ---")
import usb.core  # noqa: E402

dev = usb.core.find(bus=bus, address=address)
print(f"usb.core.find() -> {dev}")

print("\n--- Step 3: read the active configuration descriptor ---")
cfg = dev.get_active_configuration()
print(f"get_active_configuration() -> {cfg}")

print("\n--- Step 4: scan for the RAD extension unit by GUID ---")
unit_id = ffc_control._discover_rad_unit_id(device)
print(f"RAD extension unit id = {unit_id} (0x{unit_id:02x})")

print("\n--- Step 5: GET_LEN for RAD_RUN_FFC's actual control size ---")
control_len = ffc_control._query_control_len(
    device, unit_id, ffc_control.RAD_RUN_FFC_SELECTOR
)
print(
    f"RAD_RUN_FFC control length = {control_len} byte(s) "
    "(GroupGets' mapping file implies 1 -- this is what the firmware "
    "itself reports, which is what actually matters; a mismatch here is "
    "exactly what produced the earlier ENOBUFS failure)"
)

print("\n--- Step 6: send RUN_FFC via UVCIOC_CTRL_QUERY on the V4L2 node ---")
ffc_control._do_ctrl_query_ioctl(
    device,
    unit_id,
    ffc_control.RAD_RUN_FFC_SELECTOR,
    ffc_control._UVC_SET_CUR,
    size=control_len,
)
print("Sent OK -- check the app's FFC status / telemetry for confirmation.")
