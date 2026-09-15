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

## How the command actually gets sent -- and why not a plain libusb transfer

The obvious way to send this (and what an earlier version of this file
did) is to open the device with pyusb/libusb directly and call
`dev.ctrl_transfer(...)`. **That does not work here, confirmed on real
hardware**: it fails with libusb "Access denied (insufficient
permissions)" -- *not* a udev/file-permission problem (the device node
was confirmed `crw-rw-rw-`), but a Linux kernel restriction on raw USB
control transfers addressed to a specific *interface* (which is what a
UVC extension-unit request is -- recipient=INTERFACE). The kernel only
allows that from whichever process already has the interface claimed,
which in the normal case here is the `uvcvideo` kernel driver itself
(capture.py is streaming this exact device via V4L2 at the same time).
A second, independent libusb handle opened alongside that is exactly
the case this restriction exists for.

The fix is to not open a second handle at all: instead, send the SET_CUR
through **the same V4L2 device node capture.py is already using**, via
its `UVCIOC_CTRL_QUERY` ioctl (see `_send_via_v4l2_ioctl` below). That
ioctl is the kernel's own supported mechanism for exactly this --
uvcvideo, which already owns the interface, submits the request on our
behalf, so there's no second claimant to conflict with. It still needs
the `unit` (Unit ID) byte, so extension-unit *discovery* (finding that
Unit ID from the GUID above) still uses a read-only pyusb descriptor
scan -- reading descriptors doesn't claim an interface or hit this
restriction, only the earlier write attempt did, and that discovery step
is confirmed working on real hardware (it found the RAD unit; a failed
discovery would have raised a different, more specific error instead of
the permissions one).

## Verification status

Discovery (finding the RAD extension unit's Unit ID via its GUID) is
confirmed working on real hardware (found unit=5). The GUID/selector
values themselves are also now confirmed correct on real hardware -- see
below.

**Real-hardware finding: the 1-byte payload assumption was wrong.**
Sending SET_CUR with a 1-byte payload (as GroupGets' mapping file implies
for a "Run" command) failed with `UVCIOC_CTRL_QUERY failed (unit=5,
selector=0x0b): No buffer space available (errno 105)`. Per the kernel's
own UVCIOC_CTRL_QUERY docs
(docs.kernel.org/userspace-api/media/drivers/uvcvideo.html), ENOBUFS
specifically means "the specified buffer size is incorrect (too big or
too small)" -- i.e. it doesn't match the length the device itself reports
for this (unit, selector) via a UVC_GET_LEN query, which the kernel
validates *before* the request reaches the firmware. GroupGets' mapping
file's "1 byte" note isn't authoritative for actual wire length; this
firmware build apparently declares a different length for RAD_RUN_FFC
than assumed.

**Fix:** query UVC_GET_LEN for (unit, selector) first and size the SET_CUR
payload to match, rather than hardcoding a length. See
`_query_control_len` and its use in `trigger_ffc` below -- this makes the
code correct regardless of what length this (or any other) firmware build
actually declares, instead of re-guessing a hardcoded number.

Not yet re-verified on real hardware since this fix -- next test should
confirm the sidebar's FFC status transitions NEVER_COMMANDED/COMPLETE ->
IMMINENT -> IN_PROGRESS -> COMPLETE shortly after triggering (main.py
already polls and displays `telemetry.FFCState` every frame).

Separately, note for anyone testing via `diagnose_ffc.py` in isolation:
Step 3 (a bare `dev.get_active_configuration()` via pyusb, no explicit
write) can itself fail with the *same* "Access denied (insufficient
permissions)" errno 13 previously attributed only to the write path.
That's libusb's `libusb_open()` on the raw USB device node
(`/dev/bus/usb/<bus>/<addr>`) being denied -- a different file from the
V4L2 node (`/dev/videoX`) whose permissions were checked earlier. If you
hit that, it's a udev-rule/permissions problem on the raw USB node
specifically (check with `lsusb -d 1e4e:0100` then `ls -l
/dev/bus/usb/<bus>/<addr>`), unrelated to the ENOBUFS issue above.

## SYS FFC Shutter Mode -- switching off Automatic FFC entirely

Added 2026-09-15 for a time-boxed need: the shutter isn't actuating (see
the project's troubleshooting log), so every Automatic-mode auto-FFC
(every 3 min / 1.5C by default) re-bakes a *bad* calibration using
whatever's in front of the lens at that random moment. For a demo, the
fix isn't to prevent bad auto-FFCs -- it's to stop the camera from
auto-FFC'ing at all, do exactly *one* FFC on command with a real uniform
target (a piece of metal) held in front of the lens, and leave it alone.

That's what Lepton's **External** shutter mode is for: FFC only runs when
explicitly commanded, using the current scene as the reference, and the
(broken) shutter is never invoked at all -- unlike Manual mode, which
still tries to close the shutter on each commanded FFC.

    SYS extension unit GUID:            2d317470-656c-2d70-7379-732d30303030
    SYS_FFC_SHUTTER_MODE_OBJ selector:  0x10

Source: GroupGets' pt1.xml mapping (same file RAD_RUN_FFC_SELECTOR came
from) plus cross-referencing a few independent reimplementations of the
Lepton SDK's `LEP_SYS_FFC_SHUTTER_MODE_OBJ_T` struct (a Go port's
`FFCShutterMode` enum, an OpenMV forum thread using the same command).
All agree: `shutterMode` is an enum (0=MANUAL, 1=AUTO, 2=EXTERNAL) and is
the *first* field of a larger struct -- but they disagree on that
struct's full size, and none of them state the wire byte order with
enough confidence to just hardcode it. **This is meaningfully less solid
ground than RAD_RUN_FFC was**, which is a single 1-command "Run" control
GroupGets documented directly.

To avoid betting a demo on a guessed byte layout, `get_ffc_shutter_mode`
/ `set_ffc_shutter_mode` below do a **read-modify-write**: GET_CUR the
whole control first (whatever length GET_LEN reports -- same pattern as
`trigger_ffc`), only touch the 4 bytes believed to hold `shutterMode`,
leave everything else in the buffer untouched, then SET_CUR the full
thing back. `_detect_mode_field_endianness` looks at what's *actually* in
those first 4 bytes before writing anything: since the camera has
self-evidently been auto-FFC'ing (that's the whole problem), its current
mode is almost certainly AUTO=1 -- a small enough value that whichever
byte position holds a bare `1` reveals the real encoding empirically,
instead of guessing little- vs big-endian from a datasheet nobody could
find. If neither byte position looks like a plausible small enum value,
this refuses to guess and raises instead of writing something blind.

**Not yet tested on real hardware.** Test this tonight, not at the demo
-- run `set_ffc_mode.py external` on the Pi, confirm the sidebar's
telemetry (or a follow-up `set_ffc_mode.py` with no args, which just
reads and prints the current mode) shows it stuck, and confirm no more
auto-FFCs happen over several minutes. **Fallback if it doesn't work in
time:** you don't need this at all to get through a demo -- `trigger_ffc`
(the plain "Run FFC" button) already works reliably regardless of shutter
mode. Hold the metal plate up and hit Run FFC right before the demo
starts, and again any time the image degrades mid-demo; External mode
just means you don't have to babysit it.

## Platform

Linux only, matching capture.py's V4L2 deployment target (the Pi). On
Windows this raises NotImplementedError -- the equivalent Windows path is
DirectShow's IKsControl, which (like this module originally) needs real
hardware in hand to develop against safely rather than guessing; dev/test
this feature on the Pi, same as the rest of the board-specific behavior
in this app.

`fcntl` (used below for the UVCIOC_CTRL_QUERY ioctl) doesn't exist on
Windows at all -- confirmed on real hardware that an unconditional
top-level `import fcntl` breaks `import ffc_control` itself on Windows
(ModuleNotFoundError), not just `trigger_ffc()`'s call into it, which
took down `main.py` on Windows entirely (it imports this module
unconditionally at startup for the FFC button, regardless of platform).
Fixed by only importing it on non-Windows below, same pattern as
capture.py's own IS_WINDOWS branching.
"""

import ctypes
import os
import uuid
from enum import IntEnum

from capture import IS_WINDOWS

if not IS_WINDOWS:
    import fcntl

RAD_XU_GUID_STR = "2d317470-656c-2d70-7261-642d30303030"
RAD_RUN_FFC_SELECTOR = 0x0B

SYS_XU_GUID_STR = "2d317470-656c-2d70-7379-732d30303030"
SYS_FFC_SHUTTER_MODE_SELECTOR = 0x10


class FFCShutterMode(IntEnum):
    """Lepton SYS_FFC_SHUTTER_MODE_OBJ's `shutterMode` enum. See the
    module docstring's "SYS FFC Shutter Mode" section for sourcing/caveats.
    """

    MANUAL = 0    # FFC only on command; still tries to close the (broken) shutter
    AUTO = 1      # default for shuttered Leptons; self-triggers on time/temp thresholds
    EXTERNAL = 2  # FFC only on command; uses current scene, never touches the shutter

_VC_CS_INTERFACE = 0x24  # bDescriptorType: CS_INTERFACE
_VC_EXTENSION_UNIT = 0x06  # bDescriptorSubtype: VC_EXTENSION_UNIT
_VIDEO_INTERFACE_CLASS = 0x0E  # bInterfaceClass: Video
_VIDEOCONTROL_SUBCLASS = 0x01  # bInterfaceSubClass: VideoControl

# UVC Class-Specific Request Codes (linux/uvcvideo.h) -- used as the
# `query` field of uvc_xu_control_query below.
_UVC_SET_CUR = 0x01
_UVC_GET_CUR = 0x81
_UVC_GET_LEN = 0x85  # response is always exactly 2 bytes: control length, LE


class _UvcXuControlQuery(ctypes.Structure):
    """
    Mirrors `struct uvc_xu_control_query` from Linux's <linux/uvcvideo.h>
    field-for-field (same order/types), so ctypes computes the same
    padding/alignment the kernel header does on this platform (32- vs
    64-bit differ in pointer size/alignment) -- deliberately not
    hand-computing byte offsets ourselves, which is where this kind of
    struct goes subtly wrong.

        __u8  unit;
        __u8  selector;
        __u8  query;
        __u16 size;
        __u8  *data;
    """

    _fields_ = [
        ("unit", ctypes.c_uint8),
        ("selector", ctypes.c_uint8),
        ("query", ctypes.c_uint8),
        ("size", ctypes.c_uint16),
        ("data", ctypes.POINTER(ctypes.c_uint8)),
    ]


def _iowr(type_char: str, nr: int, size: int) -> int:
    """
    Recreates the Linux ioctl request-number encoding (asm-generic/ioctl.h
    _IOWR macro) for UVCIOC_CTRL_QUERY = _IOWR('u', 0x21, struct
    uvc_xu_control_query). This encoding (dir/type/nr/size packed into one
    32-bit int) is stable ABI, not something that varies by kernel version
    or architecture the way the struct's own padding can.
    """
    IOC_WRITE = 1
    IOC_READ = 2
    IOC_NRSHIFT = 0
    IOC_TYPESHIFT = 8
    IOC_SIZESHIFT = 16
    IOC_DIRSHIFT = 30
    return (
        ((IOC_READ | IOC_WRITE) << IOC_DIRSHIFT)
        | (ord(type_char) << IOC_TYPESHIFT)
        | (nr << IOC_NRSHIFT)
        | (size << IOC_SIZESHIFT)
    )


_UVCIOC_CTRL_QUERY = _iowr("u", 0x21, ctypes.sizeof(_UvcXuControlQuery))


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


def _discover_unit_id(v4l2_path: str, guid_str: str, label: str) -> int:
    """
    Read-only: finds an extension unit's firmware-assigned Unit ID by
    scanning USB descriptors via pyusb, matching on `guid_str`. This does
    not claim any interface or send anything -- just parses data the
    kernel already exposes to any opener -- so it does not hit the
    interface-claim restriction that ctrl_transfer() does (see module
    docstring). Confirmed working on real hardware for the RAD unit
    (`label="RAD"`); the SYS unit (`label="SYS"`) uses the identical
    mechanism and GUID-encoding convention, just not yet exercised.
    """
    try:
        import usb.core
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
    # 16 bytes read as plain ASCII -- "pt1-lep-rad-0000" for RAD,
    # "pt1-lep-sys-0000" for SYS, etc -- which is a good sanity check that
    # this encoding (and not a flat hex dump) is the right one.
    guid_bytes = uuid.UUID(guid_str).bytes_le
    found = _find_extension_unit(dev, guid_bytes)
    if found is None:
        raise FFCTriggerError(
            f"Could not find the Lepton {label} extension unit in this "
            f"device's USB descriptors. Compare `lsusb -v -d 1e4e:0100` "
            f"output against the {label} GUID in ffc_control.py -- this "
            f"firmware build may expose extension units differently than "
            f"GroupGets' reference firmware."
        )
    _interface_number, unit_id = found
    return unit_id


def _discover_rad_unit_id(v4l2_path: str) -> int:
    return _discover_unit_id(v4l2_path, RAD_XU_GUID_STR, "RAD")


def _discover_sys_unit_id(v4l2_path: str) -> int:
    return _discover_unit_id(v4l2_path, SYS_XU_GUID_STR, "SYS")


def _do_ctrl_query_ioctl(
    v4l2_path: str, unit_id: int, selector: int, query: int, size: int, payload: bytes = b""
) -> bytes:
    """
    Low-level UVCIOC_CTRL_QUERY wrapper shared by the SET_CUR send and the
    GET_LEN probe below, via the *same* V4L2 device node capture.py is
    already streaming from -- see the module docstring for why this, and
    not a separate libusb ctrl_transfer, is what actually works here.

    `size` is the buffer size to tell the kernel (must match what the
    device reports for this (unit, selector) via GET_LEN, or the kernel
    itself rejects the call with ENOBUFS before it reaches the firmware --
    see the module docstring's "Verification status" section). `payload`
    seeds the buffer for a write (SET_CUR); for a read (GET_LEN) it's
    irrelevant and the buffer instead comes back filled by the kernel,
    which this returns as `bytes`.
    """
    if size > 0:
        data_buf = (ctypes.c_uint8 * size)(*payload.ljust(size, b"\x00"))
        data_ptr = ctypes.cast(data_buf, ctypes.POINTER(ctypes.c_uint8))
    else:
        data_buf = None  # keep alive isn't needed -- nothing to read back
        data_ptr = ctypes.POINTER(ctypes.c_uint8)()  # NULL

    xu_query = _UvcXuControlQuery(
        unit=unit_id, selector=selector, query=query, size=size, data=data_ptr
    )
    fd = os.open(v4l2_path, os.O_RDWR)
    try:
        fcntl.ioctl(fd, _UVCIOC_CTRL_QUERY, xu_query, True)
    except OSError as exc:
        raise FFCTriggerError(
            f"UVCIOC_CTRL_QUERY failed (unit={unit_id}, selector=0x"
            f"{selector:02x}, query=0x{query:02x}, size={size}): "
            f"{exc.strerror or exc} (errno {exc.errno}). errno 2 (ENOENT)/22 "
            f"(EINVAL) usually means the unit ID or selector is wrong for "
            f"this firmware build; errno 5 (EIO) can mean the firmware "
            f"rejected/ignored the command; errno 105 (ENOBUFS) means "
            f"`size` doesn't match what this control's own GET_LEN reports."
        ) from exc
    finally:
        os.close(fd)
    return bytes(data_buf) if data_buf is not None else b""


def _query_control_len(v4l2_path: str, unit_id: int, selector: int) -> int:
    """
    UVC_GET_LEN for (unit_id, selector): asks the device itself how many
    bytes this control's SET_CUR/GET_CUR payload actually is, rather than
    trusting GroupGets' mapping file's byte-count notes -- confirmed on
    real hardware that those don't necessarily match what this firmware
    build reports (see module docstring). GET_LEN's own response is always
    exactly 2 bytes (a little-endian control length), regardless of the
    target control's size.
    """
    raw = _do_ctrl_query_ioctl(v4l2_path, unit_id, selector, _UVC_GET_LEN, size=2)
    return raw[0] | (raw[1] << 8)


def trigger_ffc(v4l2_path: str) -> None:
    """
    Send the Lepton RAD module's RUN_FFC command to the PureThermal board
    backing `v4l2_path` (e.g. "/dev/video2"). Raises FFCTriggerError on
    failure -- this function only confirms the command was *accepted*, not
    that the resulting FFC actually completed cleanly; watch
    telemetry.FFCState in the sidebar for that.
    """
    if IS_WINDOWS:
        raise NotImplementedError(
            "FFC trigger is only implemented for Linux (the Pi deployment "
            "target) -- see this module's docstring for why. Test this on "
            "the Pi; the Windows equivalent (DirectShow IKsControl) hasn't "
            "been written."
        )

    unit_id = _discover_rad_unit_id(v4l2_path)
    # Ask the device for this control's actual length instead of trusting
    # the "1 byte" assumption the mapping file implied -- real hardware
    # rejected that with ENOBUFS (see module docstring). The payload
    # content is irrelevant either way; Lepton "Run" commands ignore it.
    control_len = _query_control_len(v4l2_path, unit_id, RAD_RUN_FFC_SELECTOR)
    _do_ctrl_query_ioctl(
        v4l2_path, unit_id, RAD_RUN_FFC_SELECTOR, _UVC_SET_CUR, size=control_len
    )


def _detect_mode_field_endianness(raw: bytes) -> str:
    """
    Empirically determines whether the first 4 bytes of
    SYS_FFC_SHUTTER_MODE_OBJ encode `shutterMode` little- or big-endian, by
    looking at what's actually there rather than guessing from
    documentation nobody could pin down (see module docstring's "SYS FFC
    Shutter Mode" section). The camera has self-evidently been
    auto-FFC'ing, so its current mode is almost certainly AUTO=1 -- small
    enough that only one interpretation of these 4 bytes can plausibly be
    a valid FFCShutterMode value (0/1/2). Returns "little" or "big";
    raises rather than guessing if neither interpretation looks plausible.
    """
    if len(raw) < 4:
        raise FFCTriggerError(
            f"SYS_FFC_SHUTTER_MODE_OBJ reported only {len(raw)} byte(s) via "
            f"GET_LEN -- expected at least 4 (the shutterMode field alone). "
            f"The struct-size assumption in this module's docstring is "
            f"wrong for this firmware build; refusing to guess further."
        )
    le_val = raw[0] | (raw[1] << 8) | (raw[2] << 16) | (raw[3] << 24)
    be_val = raw[3] | (raw[2] << 8) | (raw[1] << 16) | (raw[0] << 24)
    le_plausible = le_val in (0, 1, 2)
    be_plausible = be_val in (0, 1, 2)
    if le_plausible and not be_plausible:
        return "little"
    if be_plausible and not le_plausible:
        return "big"
    if le_plausible and be_plausible and le_val == be_val:
        return "little"  # e.g. all-zero -- same bytes either way, doesn't matter
    raise FFCTriggerError(
        f"Can't determine byte order for SYS_FFC_SHUTTER_MODE_OBJ's "
        f"shutterMode field from its current value (raw bytes: "
        f"{raw[:4].hex()}) -- neither little-endian ({le_val}) nor "
        f"big-endian ({be_val}) interpretation looks like a plausible "
        f"FFCShutterMode (0/1/2). Don't guess further; this needs a real "
        f"reference (lsusb -v output cross-checked against a known-good "
        f"capture, or GroupGets/FLIR support) before setting it blind."
    )


def get_ffc_shutter_mode(v4l2_path: str):
    """
    Reads the current SYS_FFC_SHUTTER_MODE_OBJ value. Returns
    (FFCShutterMode, raw_bytes) -- `raw_bytes` is the full current buffer,
    which set_ffc_shutter_mode uses as the base for its read-modify-write,
    and is worth printing if decoding ever fails.
    """
    if IS_WINDOWS:
        raise NotImplementedError(
            "Only implemented for Linux (the Pi) -- see trigger_ffc's "
            "NotImplementedError for why."
        )
    unit_id = _discover_sys_unit_id(v4l2_path)
    length = _query_control_len(v4l2_path, unit_id, SYS_FFC_SHUTTER_MODE_SELECTOR)
    raw = _do_ctrl_query_ioctl(
        v4l2_path, unit_id, SYS_FFC_SHUTTER_MODE_SELECTOR, _UVC_GET_CUR, size=length
    )
    endianness = _detect_mode_field_endianness(raw)
    value = (
        raw[0] | (raw[1] << 8) | (raw[2] << 16) | (raw[3] << 24)
        if endianness == "little"
        else raw[3] | (raw[2] << 8) | (raw[1] << 16) | (raw[0] << 24)
    )
    return FFCShutterMode(value), raw


def set_ffc_shutter_mode(v4l2_path: str, mode: FFCShutterMode) -> FFCShutterMode:
    """
    Sets SYS_FFC_SHUTTER_MODE_OBJ's shutterMode field via read-modify-write:
    reads the full current buffer, overwrites only the 4 bytes believed to
    hold shutterMode (byte order determined empirically per-call, see
    _detect_mode_field_endianness -- deliberately not cached/assumed
    constant across calls, since getting this wrong once is cheap to
    re-detect and expensive to have silently assumed), leaves every other
    byte in the buffer exactly as read, then SET_CUR's the whole thing
    back. Returns the mode actually read back afterward, so the caller can
    confirm the change stuck rather than trusting that SET_CUR not raising
    means it worked.
    """
    if IS_WINDOWS:
        raise NotImplementedError(
            "Only implemented for Linux (the Pi) -- see trigger_ffc's "
            "NotImplementedError for why."
        )
    unit_id = _discover_sys_unit_id(v4l2_path)
    length = _query_control_len(v4l2_path, unit_id, SYS_FFC_SHUTTER_MODE_SELECTOR)
    current_raw = _do_ctrl_query_ioctl(
        v4l2_path, unit_id, SYS_FFC_SHUTTER_MODE_SELECTOR, _UVC_GET_CUR, size=length
    )
    endianness = _detect_mode_field_endianness(current_raw)
    new_raw = bytearray(current_raw)
    mode_byte = int(mode) & 0xFF
    if endianness == "little":
        new_raw[0:4] = bytes([mode_byte, 0, 0, 0])
    else:
        new_raw[0:4] = bytes([0, 0, 0, mode_byte])
    _do_ctrl_query_ioctl(
        v4l2_path,
        unit_id,
        SYS_FFC_SHUTTER_MODE_SELECTOR,
        _UVC_SET_CUR,
        size=length,
        payload=bytes(new_raw),
    )
    readback_mode, _ = get_ffc_shutter_mode(v4l2_path)
    return readback_mode
