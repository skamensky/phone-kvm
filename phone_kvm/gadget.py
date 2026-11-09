"""Builds the USB device the phone sees, through Linux's configfs gadget API. Needs root.

The same code runs on the hardware (a board with a USB device controller, such as a
Raspberry Pi's dwc2 port) and in the virtual rig (the kernel's usbip-vudc controller).
"""
import os
import time
from pathlib import Path

from . import hid

ROOT = Path("/sys/kernel/config/usb_gadget/phone-kvm")
# pid.codes test IDs. A manufactured device needs its own allocation.
VENDOR_ID, PRODUCT_ID = 0x1209, 0x0001
STRINGS = {"manufacturer": "phone-kvm", "product": "phone-kvm keyboard, touchscreen and mouse",
           "serialnumber": "0001"}


def controllers():
    return sorted(p.name for p in Path("/sys/class/udc").glob("*"))


def nodes():
    """{interface: /dev/hidgN} for the running gadget."""
    found = {}
    for name in hid.INTERFACES:
        dev = ROOT / "functions" / f"hid.{name}" / "dev"
        if dev.exists():
            found[name] = f"/dev/hidg{dev.read_text().strip().split(':')[1]}"
    return found


def up(controller=None, owner=None):
    if ROOT.exists():
        down()
    controller = controller or next(iter(controllers()), None)
    if controller is None:
        raise SystemExit("no USB device controller (/sys/class/udc is empty)")
    (ROOT / "strings/0x409").mkdir(parents=True)
    (ROOT / "idVendor").write_text(f"{VENDOR_ID:#06x}")
    (ROOT / "idProduct").write_text(f"{PRODUCT_ID:#06x}")
    (ROOT / "bcdUSB").write_text("0x0200")
    for key, value in STRINGS.items():
        (ROOT / "strings/0x409" / key).write_text(value)
    config = ROOT / "configs/c.1"
    (config / "strings/0x409").mkdir(parents=True)
    (config / "strings/0x409/configuration").write_text("HID")
    (config / "MaxPower").write_text("100")
    for name, (desc, length, subclass, protocol) in hid.INTERFACES.items():
        function = ROOT / "functions" / f"hid.{name}"
        function.mkdir(parents=True)
        (function / "protocol").write_text(str(protocol))
        (function / "subclass").write_text(str(subclass))
        (function / "report_length").write_text(str(length))
        (function / "report_desc").write_bytes(desc)
        (config / f"hid.{name}").symlink_to(function)
    (ROOT / "UDC").write_text(controller)
    found = nodes()
    for node in found.values():
        for _ in range(100):  # udev creates the nodes a moment after the bind
            if os.path.exists(node):
                break
            time.sleep(0.02)
        if owner is not None:
            os.chown(node, owner, -1)
    return {"controller": controller, "nodes": found}


def down():
    if not ROOT.exists():
        return
    if (ROOT / "UDC").read_text().strip():
        (ROOT / "UDC").write_text("\n")
    config = ROOT / "configs/c.1"
    for link in config.glob("hid.*"):
        link.unlink()
    (config / "strings/0x409").rmdir()
    config.rmdir()
    for function in (ROOT / "functions").glob("hid.*"):
        function.rmdir()
    (ROOT / "strings/0x409").rmdir()
    ROOT.rmdir()


def status():
    if not ROOT.exists():
        return {"up": False}
    controller = (ROOT / "UDC").read_text().strip()
    state = Path(f"/sys/class/udc/{controller}/state")
    return {"up": True, "controller": controller,
            "state": state.read_text().strip() if controller and state.exists() else None,
            "nodes": nodes()}


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["up", "down", "status"])
    parser.add_argument("--controller")
    parser.add_argument("--owner", type=int, help="uid that may write the HID nodes")
    args = parser.parse_args()
    result = {"up": lambda: up(args.controller, args.owner), "down": down, "status": status}[args.action]()
    print(json.dumps(result or {"up": False}))
