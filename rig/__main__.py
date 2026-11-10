"""The virtual test rig: the device and everything around it that would be hardware.

  board        the ESP32-S3 board, simulated: the real firmware (firmware/, built for the
               simulated board) running in Espressif's QEMU, with the USB controller and camera
               this repository adds to it (sim/qemu/)
  USB cable    rig/cable.py: from the board's USB port to this host's USB stack over USB/IP,
               then passed through into the phone
  phone        Google's Android emulator (headless)
  camera view  rig/camera.py: the phone as the board's camera sees it from above

  rig up | down | status      one JSON object each

The host software (phone_kvm/) is not part of the rig and does not know it is virtual: it
talks to the board over TCP as it would to the real one. adb appears here only as the oracle
the tests check against.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from phone_kvm import state
from phone_kvm.device import Device

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
FIRMWARE = ROOT / "firmware"
FLASH = FIRMWARE / "build-sim" / "flash.bin"
DEVICE = "127.0.0.1:7777"      # the board's TCP service, forwarded out of the emulated board
BOARD_USB = "127.0.0.1:7778"   # the board's USB port: a socket carrying rig/cable.py's frames
USBIP_PORT = "3241"            # not 3240, which a normal usbipd on this host would use
PICTURE = RUN / "camera" / "picture.jpg"
ACTIVITY = RUN / "usb-activity"   # touched by the cable per USB report; the camera view waits on it
# The virtual phone lies with its top towards the right of the camera's frame.
CONFIG = {"device": DEVICE, "rotate": 90}
ID = "1209", "0001"
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


def stop(name):
    value = pid(name)
    if value:
        os.killpg(value, signal.SIGTERM)
    (RUN / f"{name}.pid").unlink(missing_ok=True)


# -- USB cable -------------------------------------------------------------------------
def host_device():
    """The board as this host's USB stack sees it: its sysfs directory, or None."""
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
        if not pid("cable"):
            spawn("cable", [sys.executable, "-m", "rig.cable", "--board", BOARD_USB, "--port", USBIP_PORT,
                            "--activity", str(ACTIVITY)])
        for _ in range(50):   # until the board has pulled its USB port up
            if sudo("usbip", "--tcp-port", USBIP_PORT, "attach", "--remote", "127.0.0.1", "--busid", "1-1",
                    check=False).returncode == 0:
                break
            time.sleep(0.2)
    for _ in range(50):
        device = host_device()
        if device and drivers(device):
            break
        time.sleep(0.1)
    else:
        raise SystemExit("the board did not appear on this host's USB bus")
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
    stop("cable")


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


# -- board -----------------------------------------------------------------------------
def firmware():
    """Build the firmware for the simulated board if its sources are newer than the image."""
    sources = [FIRMWARE / "CMakeLists.txt", *FIRMWARE.glob("sdkconfig.*"), *(FIRMWARE / "main").iterdir()]
    if FLASH.exists() and FLASH.stat().st_mtime >= max(path.stat().st_mtime for path in sources):
        return
    done = subprocess.run(["nix", "develop", f"{ROOT}#firmware", "--command", str(FIRMWARE / "build.sh"), "sim"],
                          capture_output=True, text=True)
    if done.returncode:
        raise SystemExit("the firmware does not build:\n" + (done.stdout + done.stderr)[-3000:])


def device_status():
    try:
        return Device(DEVICE).status()
    except (OSError, RuntimeError):
        return None


def start_board():
    if not pid("board"):
        firmware()
        flash = RUN / "flash.bin"   # the board's own flash chip: it writes to it
        flash.write_bytes(FLASH.read_bytes())
        PICTURE.parent.mkdir(parents=True, exist_ok=True)
        host, port = BOARD_USB.split(":")
        spawn("board", ["qemu-system-xtensa", "-M", "esp32s3", "-nographic", "-monitor", "none",
                        "-serial", f"file:{BUILD / 'board-console.log'}",
                        "-drive", f"file={flash},if=mtd,format=raw",
                        "-chardev", f"socket,id=usb,host={host},port={port},server=on,wait=off,nodelay=on",
                        "-global", "driver=esp32s3.usb_otg,property=chardev,value=usb",
                        "-global", f"driver=esp32s3.simcam,property=file,value={PICTURE}",
                        "-nic", f"user,model=open_eth,hostfwd=tcp:{DEVICE}-:{DEVICE.split(':')[1]}"])
    for _ in range(150):
        if device_status():
            return
        time.sleep(0.2)
    raise SystemExit(f"the board did not start; see {BUILD / 'board-console.log'}")


def start_camera(sensor):
    if not pid("camera"):
        PICTURE.unlink(missing_ok=True)
        spawn("camera", [sys.executable, "-m", "rig.camera", "--adb", ADB, "--serial", SERIAL,
                         "--picture", str(PICTURE), "--scratch", str(RUN / "camera" / "display"),
                         "--activity", str(ACTIVITY), "--size", sensor])
    for _ in range(100):
        if PICTURE.exists():
            break
        time.sleep(0.2)
    else:
        raise SystemExit(f"the camera view did not start; see {BUILD / 'camera.log'}")
    state.save("config", CONFIG)
    for record in ("geometry", "calibration", "session"):
        state.save(record, {})


# -- commands --------------------------------------------------------------------------
def up(sensor):
    RUN.mkdir(parents=True, exist_ok=True)
    sudo("modprobe", "vhci-hcd")
    start_board()
    plug_usb()
    start_phone()
    start_camera(sensor)
    return status()


def down():
    stop("camera")
    stop_phone()
    unplug_usb()
    stop("board")
    return status()


def status():
    device = host_device()
    board = device_status() if pid("board") else None
    phone_up = bool(pid("emulator")) and booted()
    sees = phone_up and f"vendor=0x{ID[0]}, product=0x{ID[1]}" in adb("shell", "dumpsys", "input", check=False).stdout
    return {
        "board": {"running": bool(pid("board")), "device": DEVICE, "status": board},
        "usb_cable": {"attached": device is not None, "interface_drivers": drivers(device) if device else None},
        "phone": {"serial": SERIAL, "booted": phone_up, "sees_device": sees},
        "camera_view": {"picture": str(PICTURE), "running": bool(pid("camera"))},
        "ready": bool(board and board["plugged"] and sees and pid("camera")),
    }


def main():
    parser = argparse.ArgumentParser(prog="rig", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["up", "down", "status"])
    parser.add_argument("--camera", default="1600x1200", help="the camera's sensor resolution (up)")
    args = parser.parse_args()
    print(json.dumps({"up": lambda: up(args.camera), "down": down, "status": status}[args.command](), indent=1))


if __name__ == "__main__":
    main()
