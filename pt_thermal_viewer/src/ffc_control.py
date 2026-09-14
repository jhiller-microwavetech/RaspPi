"""
Manual FFC (flat-field correction) trigger for the PureThermal board, sent
as a Lepton CCI command over the UVC device's Extension Unit (XU).

## Why this needs to exist at all

capture.py talks to the board purely as a UVC webcam (plain V4L2/OpenCV) --
the board's own STM32F412 firmware does all the VoSPI/CCI work with the
Lepton and just hands the host finished frames. Triggering FFC from the
host means reaching *past* that plain video path into the UVC Extension
Unit interface -- a standard, separate part of the USB Video Class spec
for vendor-specific controls, sent as USB control transfers rather than
video data.

## Where the constants below come from

The PureThermal firmware exposes *one XU per Lepton SDK module*
(AGC/OEM/RAD/SYS/VID), each with its own 16-byte GUID, and maps each
Lepton CCI register/command to its own single-byte "control selector"
within its module's XU. Source: GroupGets' own v4l2/uvcdynctrl control
mapping (github.com/groupgets/purethermal1-uvc-capture,
v4l2/uvcdynctrl/pt1.xml) and the firmware wiki page "Lepton CCI through
UVC extension units" (github.com/groupgets/purethermal1-firmware/wiki),
which documents that Lepton "Run" commands (RUN_FFC included) are 1-byte
write-only controls -- a bare SET_CUR with 1 byte of throwaway data
executes the command; no GET_CUR/readback is needed to fire it.

We use the *RAD* module's RUN_FFC (control id RAD_RUN_FFC in that mapping
file), not SYS's -- this board is configured for Radiometry/TLinear (see
capture.py's module docstring), and RAD's FFC is the one that updates the
radiometric FFC metadata (fpa_temp_at_last_ffc_k / time_at_last_ffc_ms in
telemetry.py) that this app already reads out.

    RAD extension unit GUID:      2d317470-656c-2d70-7261-642d30303030
    RAD_RUN_FFC control selector: 0x0B  (1 byte, SET_CUR only)

## Why there's no hardcoded Unit ID

A UVC Extension Unit has two identifying numbers: its 16-byte GUID (fixed,
listed above) and a small integer "Unit ID" the firmware assigns when it
builds its USB descriptors -- *that* part is genuinely firmware-build
specific (this is what the original CLAUDE.md note was cautious about).
Rather than hardcode a Unit ID that might be wrong for your firmware
build, `_find_extension_unit` below reads the connected device's actual
USB Video Class descriptors at runtime and matches on the GUID -- the
same thing tools like `uvcdynctrl`/libuvc do internally. That makes this
correct regardless of which Unit ID a given firmware build happens to
assign, with no external config file or tool required.

## Verification status

*** Not yet verified against real hardware. *** Two things here could
still be wrong for this specific board/firmware build:

  1. Whether `_find_extension_unit` finds the RAD XU at all -- it assumes
     the descriptor appears in the VideoControl interface's class-specific
     descriptors, which is true for every compliant UVC device, but worth
     confirming against real output if it fails.
  2. The RAD_RUN_FFC selector (0x0B) and GUID above, transcribed from
     GroupGets' mapping file.

A wrong selector on a *matched* unit is a safe failure: Lepton "Run"
commands only respond to a bare SET_CUR at this exact selector, so a
wrong value either gets rejected (USBError) or silently ignored by the
firmware -- it does not write to some other register. If the GUID match
in part (1) fails, trigger_ffc raises a clear RuntimeError rather than
guessing at a Unit ID.

To verify on real hardware: run this with the board attached, and confirm
the sidebar's FFC status transitions NEVER_COMMANDED/COMPLETE -> IMMINENT
-> IN_PROGRESS -> COMPLETE shortly after triggering (main.py already
polls and displays `telemetry.FFCState` every frame). If it raises
"could not find the Lepton RAD extension unit", compare `lsusb -v -d
1e4e:0100` output's VC_EXTENSION_UNIT descriptors against RAD_XU_GUID
below -- this firmware build's XU layout may differ from GroupGets'
reference firmware.

## Platform

Linux only, matching capture.py's V4L2 deployment target (the Pi). On
Windows this raises NotImplementedError -- the equivalent Windows path is
DirectShow's IKsControl, which (like this module originally) needs real
hardware in hand to develop against safely rather than guessing; dev/test
this feature on the Pi, same as the rest of the board-specific behavior
in this app.
"""

import os
import uuid

from capture import IS_WINDOWS

RAD_XU_GUID_STR = "2d317470-656c-2d70-7261-642d30303030"
RAD_RUN_FFC_SELECTOR = 0x0B

_VC_CS_INTERFACE = 0x24  # bDescriptorType: CS_INTERFACE
_VC_EXTENSION_UNIT = 0x06  # bDescriptorSubtype: VC_EXTENSION_UNIT
_VIDEO_INTERFACE_CLASS = 0x0E  # bInterfaceClass: Video
_VIDEOCONTROL_SUBCLASS = 0x01  # bInterfaceSubClass: VideoControl
_UVC_SET_CUR = 0x01


class FFCTriggerError(RuntimeError):
    """Raised when the FFC command couldn't be found or sent on this device."""


def _usb_bus_address_for_v4l2(v4l2_path: str):
    """
    Resolve a /dev/videoX node to the (bus, address) of the underlying USB
    device by walking up its sysfs `device` symlink until we hit a
    directory that has both `busnum` and `devnum` -- that's the USB device
    itself, as opposed to one of its several interfaces (a UVC device
    exposes at least VideoControl + VideoStreaming as separate interface
    sysfs nodes below the device node).
    """
    name = os.path.basename(v4l2_path.rstrip("/"))
    d = os.path.realpath(f"/sys/class/video4linux/{name}/device")
    for _ in range(6):  # bounded walk; a real sysfs tree is only a few levels deep here
        busnum_path = os.path.join(d, "busnum")
        devnum_path = os.path.join(d, "devnum")
        if os.path.exists(busnum_path) and os.path.exists(devnum_path):
            with open(busnum_path) as f:
                bus = int(f.read().strip())
            with open(devnum_path) as f:
                address = int(f.read().strip())
            return bus, address
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    raise FFCTriggerError(
        f"Could not resolve a USB bus/address for {v4l2_path} via sysfs "
        f"(looked under /sys/class/video4linux/{name}/device). Is this "
        f"really the PureThermal's video node?"
    )


def _find_extension_unit(dev, guid_bytes: bytes):
    """
    Scan `dev`'s active configuration for a VideoControl interface, then
    its class-specific descriptors, for a VC_EXTENSION_UNIT descriptor
    whose 16-byte GUID matches `guid_bytes`.

    Returns (interface_number, unit_id) or None.

    Descriptor layout (USB Video Class 1.1 spec, 3.7.2.5):
      offset 0:  bLength
      offset 1:  bDescriptorType      (0x24, CS_INTERFACE)
      offset 2:  bDescriptorSubtype   (0x06, VC_EXTENSION_UNIT)
      offset 3:  bUnitID
      offset 4:  guidExtensionCode[16]
      offset 20: bNumControls
      offset 21: bNrInPins (P)
      offset 22..22+P-1: baSourceID[P]
      offset 22+P: bControlSize (N)
      ... (bmControls, iExtension follow; not needed here)
    """
    cfg = dev.get_active_configuration()
    for intf in cfg:
        if (
            intf.bInterfaceClass != _VIDEO_INTERFACE_CLASS
            or intf.bInterfaceSubClass != _VIDEOCONTROL_SUBCLASS
        ):
            continue
        extra = bytes(intf.extra_descriptors)
        i = 0
        while i + 3 <= len(extra):
            length = extra[i]
            if length == 0:
                break
            descriptor_type = extra[i + 1]
            subtype = extra[i + 2]
            if (
                descriptor_type == _VC_CS_INTERFACE
                and subtype == _VC_EXTENSION_UNIT
                and length >= 20
            ):
                unit_id = extra[i + 3]
                found_guid = extra[i + 4 : i + 20]
                if bytes(found_guid) == guid_bytes:
                    return intf.bInterfaceNumber, unit_id
            i += length
    return None


def trigger_ffc(v4l2_path: str) -> None:
    """
    Send the Lepton RAD module's RUN_FFC command to the PureThermal board
    backing `v4l2_path` (e.g. "/dev/video2"). Raises FFCTriggerError (or
    lets a usb.core.USBError propagate) on failure -- this function only
    confirms the USB command was *accepted*, not that the resulting FFC
    actually completed cleanly; watch telemetry.FFCState in the sidebar
    for that.
    """
    if IS_WINDOWS:
        raise NotImplementedError(
            "FFC trigger is only implemented for Linux (the Pi deployment "
            "target) -- see this module's docstring for why. Test this on "
            "the Pi; the Windows equivalent (DirectShow IKsControl) hasn't "
            "been written."
        )

    try:
        import usb.core
        import usb.util
    except ImportError as exc:
        raise FFCTriggerError(
            "pyusb isn't installed. On the Pi: `sudo apt install python3-usb` "
            "(or `pip install pyusb` plus a libusb1 backend)."
        ) from exc

    bus, address = _usb_bus_address_for_v4l2(v4l2_path)
    try:
        dev = usb.core.find(bus=bus, address=address)
    except usb.core.NoBackendError as exc:
        raise FFCTriggerError(
            "pyusb has no libusb backend available. On the Pi: "
            "`sudo apt install libusb-1.0-0` (python3-usb pulls this in "
            "already; only needed if you installed pyusb via pip instead)."
        ) from exc
    if dev is None:
        raise FFCTriggerError(
            f"No USB device found at bus {bus} address {address} for "
            f"{v4l2_path} -- it may have re-enumerated; try reopening the "
            f"camera and triggering again."
        )

    # Standard mixed-endian GUID encoding (fields 1-3 byte-reversed, 4-5
    # literal) -- same convention `uuid.UUID.bytes_le` implements, and the
    # one USB/UVC descriptor GUIDs use. Worth knowing if you ever need to
    # eyeball this against `lsusb -v` output by hand: decoded this way, the
    # 16 bytes read as plain ASCII -- "pt1-lep-rad-0000" for this one,
    # "pt1-lep-sys-0000" for the SYS unit, etc -- which is a good sanity
    # check that this encoding (and not a flat hex dump) is the right one.
    guid_bytes = uuid.UUID(RAD_XU_GUID_STR).bytes_le
    found = _find_extension_unit(dev, guid_bytes)
    if found is None:
        raise FFCTriggerError(
            "Could not find the Lepton RAD extension unit in this device's "
            "USB descriptors. Compare `lsusb -v -d 1e4e:0100` output "
            "against RAD_XU_GUID_STR in ffc_control.py -- this firmware "
            "build may expose extension units differently than GroupGets' "
            "reference firmware."
        )
    interface_number, unit_id = found

    bm_request_type = usb.util.build_request_type(
        usb.util.CTRL_OUT, usb.util.CTRL_TYPE_CLASS, usb.util.CTRL_RECIPIENT_INTERFACE
    )
    w_value = RAD_RUN_FFC_SELECTOR << 8
    w_index = (unit_id << 8) | interface_number
    # 1 byte of throwaway data -- Lepton "Run" commands ignore the payload,
    # per the firmware wiki (see module docstring).
    dev.ctrl_transfer(bm_request_type, _UVC_SET_CUR, w_value, w_index, b"\x00")
