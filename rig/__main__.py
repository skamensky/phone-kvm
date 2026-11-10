"""The virtual test rig: everything around the device that would be hardware.

  phone        Google's Android emulator (headless)
  USB cable    the device's gadget on the kernel's virtual controller (usbip-vudc), attached
               to this host over USB/IP, then passed through into the emulator
  HDMI cable   the emulator's display written to a V4L2 loopback device (rig/hdmi.py)
  camera       the phone as a camera above it sees it, on a second loopback device (rig/camera.py)

  rig up | down | status      one JSON object each

The device software (phone_kvm/) is not part of the rig and does not know it is virtual.
adb appears here only as the cable and as the oracle the tests check against.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from phone_kvm import gadget, state

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
RUN = BUILD / "run"
SDK = Path(os.environ["ANDROID_HOME"])
ADB = str(SDK / "platform-tools/adb")
EMULATOR = str(SDK / "emulator/emulator")
IMAGE = "system-images;android-36;google_apis;x86_64"
AVD = "phone"
CONSOLE_PORT = 5580
SERIAL = f"emulator-{CONSOLE_PORT}"
CONTROLLER = "usbip-vudc.0"
USBIP_PORT = "3241"  # not 3240, which a normal usbipd on this host would use
VIDEO_NR, CAMERA_NR = 42, 43
VIDEO, CAMERA = f"/dev/video{VIDEO_NR}", f"/dev/video{CAMERA_NR}"
HDMI_VIEW = {"video": VIDEO, "view": "hdmi", "rotate": 0, "size": None, "input_format": None}
# The virtual phone lies with its top towards the right of the camera's frame.
CAMERA_VIEW = {"video": CAMERA, "view": "camera", "rotate": 90, "size": None, "input_format": None}
ID = f"{gadget.VENDOR_ID:04x}", f"{gadget.PRODUCT_ID:04x}"
ENV = {**os.environ, "ANDROID_AVD_HOME": str(BUILD / "avd"), "ANDROID_USER_HOME": str(BUILD / "android-home")}


def sh(*cmd, check=True, **kw):
    return subprocess.run(cmd, check=check, capture_output=True, text=True, env=ENV, **kw)


def sudo(*cmd, **kw):
    return sh("sudo", "-n", *cmd, **kw)


def adb(*cmd, **kw):
    return sh(ADB, "-s", SERIAL, *cmd, **kw)


def pid(name):
    path = RUN / f"{name}.pid"
    if path.exists():
        value = int(path.read_text())
        if Path(f"/proc/{value}").exists():
            return value
    return None


def spawn(name, cmd):
    log = (BUILD / f"{name}.log").open("w")
    process = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=ENV, start_new_session=True)
    (RUN / f"{name}.pid").write_text(str(process.pid))


def stop(name, root=False):
    value = pid(name)
    if value:
        if root:
            sudo("kill", str(value), check=False)
        else:
            os.killpg(value, signal.SIGTERM)
    (RUN / f"{name}.pid").unlink(missing_ok=True)


# -- USB cable -------------------------------------------------------------------------
def host_device():
    """The gadget as this host's USB stack sees it: its sysfs directory, or None."""
    for path in Path("/sys/bus/usb/devices").glob("*"):
        vendor = path / "idVendor"
        if (vendor.exists() and (vendor.read_text().strip(), (path / "idProduct").read_text().strip()) == ID
                and "vhci_hcd" in str(path.resolve())):
            return path
    return None


def drivers(device):
    return {i.name: (os.path.basename(os.readlink(i / "driver")) if (i / "driver").exists() else None)
            for i in sorted(device.glob(f"{device.name}:*"))}


def plug_usb():
    if host_device() is None:
        if not pid("usbipd"):
            sudo("usbipd", "--device", "--daemon", "--tcp-port", USBIP_PORT, "--pid", str(RUN / "usbipd.pid"))
            time.sleep(0.5)
        sudo("usbip", "--tcp-port", USBIP_PORT, "attach", "--remote", "127.0.0.1", "--device", CONTROLLER)
    for _ in range(50):
        device = host_device()
        if device and drivers(device):
            break
        time.sleep(0.1)
    else:
        raise SystemExit("the gadget did not appear on this host's USB bus")
    # This host must never use the device as its own keyboard and touchscreen: detach its HID
    # driver. The emulator then claims the interfaces through usbfs.
    time.sleep(0.5)
    for interface, driver in drivers(device).items():
        if driver == "usbhid":
            sudo("sh", "-c", f"echo {interface} > /sys/bus/usb/drivers/usbhid/unbind")
    node = f"/dev/bus/usb/{int((device / 'busnum').read_text()):03d}/{int((device / 'devnum').read_text()):03d}"
    sudo("chown", str(os.getuid()), node)


def unplug_usb():
    ports = sudo("usbip", "port", check=False).stdout
    port = None
    for line in ports.splitlines():
        if line.startswith("Port "):
            port = line.split()[1].rstrip(":")
        elif ":".join(ID) in line and port is not None:
            sudo("usbip", "detach", "--port", str(int(port)), check=False)
    stop("usbipd", root=True)


# -- phone -----------------------------------------------------------------------------
def booted():
    return adb("shell", "getprop", "sys.boot_completed", check=False).stdout.strip() == "1"


def start_phone():
    if not (BUILD / "avd" / f"{AVD}.avd").exists():
        (BUILD / "avd").mkdir(parents=True, exist_ok=True)
        avdmanager = next((SDK / "cmdline-tools").glob("*/bin/avdmanager"))
        sh(str(avdmanager), "create", "avd", "--name", AVD, "--package", IMAGE, "--device", "pixel_7",
           input="no\n")
    if not pid("emulator"):
        spawn("emulator", [EMULATOR, "-avd", AVD, "-port", str(CONSOLE_PORT), "-no-window", "-no-audio",
                           "-no-boot-anim", "-no-snapshot", "-gpu", "swiftshader_indirect",
                           "-usb-passthrough", f"vendorid=0x{ID[0]},productid=0x{ID[1]}"])
    deadline = time.monotonic() + 300
    while not booted():
        if time.monotonic() > deadline or not pid("emulator"):
            raise SystemExit(f"the emulator did not boot; see {BUILD / 'emulator.log'}")
        time.sleep(2)


def stop_phone():
    if pid("emulator"):
        adb("emu", "kill", check=False)
        for _ in range(100):
            if not pid("emulator"):
                break
            time.sleep(0.1)
    stop("emulator")


# -- HDMI cable and camera -----------------------------------------------------------
def plug_video(hdmi, camera):
    if not (Path(VIDEO).exists() and Path(CAMERA).exists()):
        stop("hdmi")
        sudo("modprobe", "-r", "v4l2loopback", check=False)
        sudo("modprobe", "v4l2loopback", f"video_nr={VIDEO_NR},{CAMERA_NR}", "exclusive_caps=1,1",
             "card_label=phone-kvm virtual HDMI capture,phone-kvm virtual camera")
    if not pid("hdmi"):
        spawn("hdmi", [sys.executable, "-m", "rig.hdmi", "--adb", ADB, "--serial", SERIAL,
                       "--device", VIDEO, "--size", hdmi])
    if not pid("camera"):
        spawn("camera", [sys.executable, "-m", "rig.camera", "--adb", ADB, "--serial", SERIAL,
                         "--device", CAMERA, "--scratch", str(RUN / "camera"), "--size", camera])
    state.save("config", HDMI_VIEW)
    for record in ("geometry", "calibration", "session"):
        state.save(record, {})


# -- commands --------------------------------------------------------------------------
def up(hdmi, camera):
    RUN.mkdir(parents=True, exist_ok=True)
    sudo("modprobe", "-a", "libcomposite", "usbip-vudc", "vhci-hcd")
    if not gadget.status()["up"]:
        sudo("env", f"PYTHONPATH={ROOT}", sys.executable, "-m", "phone_kvm.gadget", "up",
             "--controller", CONTROLLER, "--owner", str(os.getuid()))
    plug_usb()
    start_phone()
    plug_video(hdmi, camera)
    return status()


def down():
    stop("hdmi")
    stop("camera")
    stop_phone()
    unplug_usb()
    sudo("env", f"PYTHONPATH={ROOT}", sys.executable, "-m", "phone_kvm.gadget", "down")
    return status()


def status():
    device = host_device()
    phone_up = bool(pid("emulator")) and booted()
    sees = phone_up and f"vendor=0x{ID[0]}, product=0x{ID[1]}" in adb("shell", "dumpsys", "input", check=False).stdout
    return {
        "gadget": gadget.status(),
        "usb_cable": {"attached": device is not None, "interface_drivers": drivers(device) if device else None},
        "phone": {"serial": SERIAL, "booted": phone_up, "sees_device": sees},
        "hdmi_cable": {"device": VIDEO, "running": bool(pid("hdmi"))},
        "camera": {"device": CAMERA, "running": bool(pid("camera"))},
        "ready": bool(sees and pid("hdmi") and pid("camera")),
    }


def main():
    parser = argparse.ArgumentParser(prog="rig", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["up", "down", "status"])
    parser.add_argument("--hdmi", default="1920x1080", help="HDMI frame size (up)")
    parser.add_argument("--camera", default="1600x1200", help="camera sensor resolution (up)")
    args = parser.parse_args()
    print(json.dumps({"up": lambda: up(args.hdmi, args.camera), "down": down, "status": status}[args.command](), indent=1))


if __name__ == "__main__":
    main()
