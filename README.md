# phone-kvm

Gives an agent a real Android phone to use the way a person does. A small ESP32-S3 board
plugs into the phone's USB port as a keyboard, touchscreen and mouse, is powered by the phone,
and looks at the phone's screen with its own camera. Nothing is installed on the phone: no
app, no developer mode, no adb. Because the view is a camera, it works on any Android phone
and on every screen, lock screens included. Design: [docs/design.md](docs/design.md).

| Part | Where |
| --- | --- |
| Firmware (ESP-IDF, C) | `firmware/` |
| The board (KiCad, generated from `hardware/circuit.py`) | `hardware/`, `tools/` |
| The simulated board (devices added to Espressif's QEMU) | `sim/qemu/` |
| Host software: `phonectl` | `phone_kvm/` |
| The virtual rig around the simulated board, and the tests | `rig/`, `tests/` |

The board has not been manufactured yet. The same firmware runs here on a simulated ESP32-S3
whose USB port is plugged into a virtual phone (the Android emulator) and whose camera looks
at that phone's screen.

## Virtual rig

Needs Linux with KVM, passwordless `sudo`, Nix, the stock kernel module `vhci-hcd` and
`usbip` (Ubuntu: `linux-tools-generic`). The flake accepts the Android SDK licence.

    bin/rig up        # builds the firmware, boots the board, plugs it into the phone;
                      # about a minute; prints status with "ready": true
    bin/rig status
    bin/rig down
    bin/check         # host logic, and everything end to end when the rig is up (3 minutes)

After `rig up`, find the screen and measure the pointer once:

    bin/phonectl key meta+i && bin/phonectl calibrate screen    # a light screen: Settings
    bin/phonectl key home && bin/phonectl calibrate pointer

### A real phone in the rig

The simulated board can drive a real phone that has USB debugging on, over USB or Wi-Fi, with
a webcam on this host as the board's camera:

    bin/rig up --phone real --serial 10.0.0.5:5555 --webcam /dev/video0 --rotate 0

`--serial` is the phone as `adb devices` names it. The phone gets the board's four HID
interfaces as devices in its own kernel (`rig/uhid.py`). `python -m rig.view` serves the
camera's picture to a browser, for aiming the webcam.

## Using the phone

Every command prints one JSON object. Positions are pixels of the screenshot.

    bin/phonectl screenshot shot.png        # never older than the last input; waits for the
                                            # screen to settle (--settle-ms 0 to not)
    bin/phonectl tap 243 990                # --hold-ms 800 for a long press
    bin/phonectl swipe 243 800 243 300      # more points trace a path: an unlock pattern
    bin/phonectl pinch 243 500 --start 80 --end 300
    bin/phonectl type 'Hello, World!'
    bin/phonectl key enter                  # home back recents power volume-up ... ctrl+a meta+n
    bin/phonectl mouse to 187 715           # also: move DX DY, click [right], scroll N
    bin/phonectl calibrate                  # after setup, and after the phone or camera moves
    bin/phonectl frame raw.png              # the camera's whole frame, for aiming it
    bin/phonectl status

The loop for an agent is: `screenshot`, read the image, act, `screenshot`.

`mouse to` looks where the cursor landed and corrects it, so it takes a few seconds and answers
with `pointer` and `seen` (false when the cursor could not be found on the screen).

`screenshot` also reports `captured_at`, `ms_since_input`, `settled` and `pointer`: where the
mouse cursor is in that image, or null when it is not known. `calibrate` measures what ties
the image to the inputs: where the screen is in the camera's frame, how far the cursor moves
per mouse count, how long an input takes to show, and the camera's noise. `calibrate screen`
and `calibrate pointer` do the halves separately. The screen is found as a lit quadrilateral,
so `calibrate screen` needs a light screen that reaches all four edges; `calibrate pointer`
needs a screen that does not react to hovering (the home screen away from icons).

Unlocking: `key power` wakes the phone, a swipe up opens the entry screen, then `type` the
PIN or password and `key enter`, or `swipe` through the pattern's dots.

`bin/*` run inside the project's Nix shell; set `PHONE_KVM_SHELL=1` when already in it
(`nix develop`) to skip the start-up cost.

## The board

    nix develop .#board --command python3 tools/generate.py   # hardware/circuit.py -> schematic and PCB, routed
    nix develop .#board --command python3 tools/board.py      # ERC, DRC, order files, pictures

`tools/board.py` writes the JLCPCB order files to `build/fab/` and pictures of the board to
`build/board/`. The board is 32 x 50 mm, two layers, about $7 of parts. Edit the circuit in `hardware/circuit.py`, never the KiCad files.

## On the real board

    nix develop .#firmware --command firmware/build.sh board
    nix develop .#firmware --command idf.py -C firmware -B firmware/build-board -p /dev/ttyUSB0 flash monitor

The USB-C port belongs to the phone, so flashing and the console use the UART header with a
3.3 V USB serial adapter (hold BOOT, press RESET to enter the bootloader). On the console:

    wifi <network name> <password>
    token <16 or more characters>
    status

Then, from the computer the agent runs on, with the token in the environment (never in a file):

    export PHONE_KVM_TOKEN=...
    bin/phonectl setup --device <the board's address> --rotate 90
    bin/phonectl frame raw.png      # aim the camera, then calibrate as above

`--rotate` is how far clockwise the phone appears turned in the camera's frame. The phone's
screen should fill most of the frame, on a dark surface, without glare.
