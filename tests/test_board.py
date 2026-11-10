"""The board's circuit against the firmware that will run on it: the same pins on both sides."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware"))
import circuit  # noqa: E402

FIRMWARE = ROOT / "firmware" / "main"
# The camera driver's pin names for the connector's signals.
DRIVER = {"SIOD": "sccb_sda", "SIOC": "sccb_scl", "VSYNC": "vsync", "HREF": "href", "XCLK": "xclk", "PCLK": "pclk",
          **{f"Y{n}": f"d{n - 2}" for n in range(2, 10)}}


def test_the_camera_is_wired_to_the_pins_the_firmware_drives():
    source = (FIRMWARE / "camera_ov2640.c").read_text()
    firmware = {name: int(pin) for name, pin in re.findall(r"\.pin_(\w+) = (-?\d+)", source)}
    board = {DRIVER[signal]: gpio for signal, gpio in circuit.CAMERA.values()}
    assert board == {name: pin for name, pin in firmware.items() if pin >= 0}


def test_the_status_led_is_on_the_pin_the_firmware_drives():
    default = re.search(r"config PHONE_KVM_LED_GPIO.*?default (\d+)", (FIRMWARE / "Kconfig.projbuild").read_text(), re.S)
    assert f"U1.IO{default.group(1)}" in circuit.NETS["LED"]


def test_every_net_joins_pins_of_parts_that_exist_and_no_pin_is_on_two_nets():
    seen = {}
    for net, pins in circuit.NETS.items():
        for pin in pins:
            assert pin.split(".")[0] in circuit.PARTS, pin
            assert pin not in seen, (pin, net, seen[pin])
            seen[pin] = net
    assert not set(circuit.NO_CONNECT) & set(seen)
