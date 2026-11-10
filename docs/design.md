# phone-kvm design

## Goal

Let an agent use a real Android phone exactly as a person does: look at the screen, touch it,
type, press the hardware keys, use a mouse. Nothing is installed on the phone, no developer
mode, no adb. The phone sees a USB keyboard, touchscreen and mouse, which every Android
supports out of the box, and a camera looks at its screen as eyes would. Whatever a person
can do with fingers and eyes, the agent can do, including unlocking with a PIN, password or
pattern.

The view is a camera, not the phone's video output, for two reasons: most Android phones have
no video output on USB-C, and Android blanks secure windows (lock-screen entry, banking apps)
on the ones that do.

## Shape

```
                  Wi-Fi, TCP                      USB-C: data and power
   agent ─── phonectl ───────────►  board  ───────────────────────────►  phone
  (any computer)                  (ESP32-S3) ◄─ ─ ─ camera looks at ─ ─ ─ screen
```

| Part | Where | Job |
| --- | --- | --- |
| Firmware | `firmware/` | Four USB HID interfaces, the camera, a TCP service; replays input with its own timing |
| Board | `hardware/`, `tools/` | ESP32-S3 module, USB-C device port, camera connector, power |
| Host | `phone_kvm/` | `phonectl`: finds the screen in the camera's picture, turns screenshot pixels into input |

The board is deliberately dumb: it relays HID reports and takes pictures. Everything that
needs thought (where the screen is, coordinates, calibration, waiting for the screen to
settle) is Python on the agent's computer.

### USB interfaces

| Interface | Reports | Android sees | Used for |
| --- | --- | --- | --- |
| keyboard | boot keyboard, 8 bytes | external keyboard | text, shortcuts, PIN and password entry |
| touch | 5-contact digitizer, absolute 0..32767, 31 bytes | direct touchscreen (no cursor) | tap, long press, swipe, multi-point path, pinch |
| mouse | 3 buttons, relative 16-bit X/Y, wheel, 6 bytes | mouse with a cursor | hover, right click, wheel |
| consumer | one 16-bit usage, 2 bytes | system keys | home, back, power, volume, media (recents is the keyboard's Meta+Tab) |

The descriptors live in `firmware/main/usb.c`; the host builds the reports
(`phone_kvm/hid.py`). A test reads the descriptors back from the phone's kernel and checks
that they describe exactly the reports the host sends.

Touch is the primary pointer: it is absolute, so a screenshot pixel maps straight to a touch
position with no feedback loop. The mouse exists because some things only a mouse does
(hover, right click, wheel).

### The device's TCP service

One port (7777). A request is an operation byte, a 4-byte little-endian length and a payload;
a reply is 0 (done) or 1 (failed), a length and a payload.

| Operation | Payload | Reply |
| --- | --- | --- |
| `A` | the token | |
| `H` | input: records of `wait ms (u16), interface (u8), length (u8), report` | when the last report has been sent |
| `C` | | one JPEG from the camera |
| `S` | | JSON: firmware, simulated, plugged, keyboard LEDs |

A gesture is sent as one `H` batch and the board paces the reports itself, so the network's
jitter never reaches the phone: a swipe is as smooth over Wi-Fi as over a wire. The real
board refuses everything until `A` has given the token it holds in flash; the token and the
Wi-Fi credentials are set on its UART console and never appear in a file on the host
(`PHONE_KVM_TOKEN` in the environment).

### Coordinates

Every position `phonectl` takes is a pixel of the image `phonectl screenshot` writes. A
screenshot pixel is scaled to the digitizer's 0..32767 range, which Android maps onto the
whole display.

### The view

`phone_kvm/camera.py` finds the lit screen's four corners in the camera's picture (threshold,
largest lit region, a line fitted to each edge at sub-pixel precision) and maps that
quadrilateral back to an upright rectangle. Four pictures are averaged, which halves sensor
noise. The camera and phone must not move after calibration, and the screen must be light and
reach all four edges while it is found: a dark wallpaper cannot be told from the bezel.

### Calibration: where, pointer, when

Three stored measurements tie the image an agent sees to the inputs it sends
(`phonectl calibrate`, shown by `status`):

- **Where** (`geometry`): the screen's corners in the frame, and the screenshot size.
- **Pointer** (`pixels_per_count`, `offset`): the mouse is relative, and Android scales its
  movement by the cursor's speed over the last 100 ms. A report that arrives after a pause
  is scaled by a constant, so every move is one report (X and Y are 16-bit, so one report
  reaches anywhere) sent after 400 ms of stillness. Calibration runs the cursor into the
  top-left corner, steps it out along a diagonal, finds its tip at each stop by differencing
  screenshots (ignoring areas that change by themselves, such as the clock) and fits a line.
  `mouse to` uses the fit only to get near: on a real phone under a camera it was 5% out
  down the screen, 70 pixels at the bottom. It then moves the cursor aside, finds it as the
  two differences between the pictures before and after that lie the move apart, and moves
  it by what is missing, looking again after any move long enough to see. It reports
  `pointer` and `seen`; unseen (a screen that changes by itself), the cursor is left where
  the fit put it. The session tracks the cursor from there by the counts it sends;
  `screenshot` reports it as `pointer`, null until `mouse to` or calibration has placed it.
- **When** (`latency_ms`, `noise`): the time from an input to the first picture that shows
  all of it, measured with cursor jumps, and how much two pictures of a still screen differ.
  `screenshot` does not capture before the last input's latency has passed, so an image
  never predates the input it follows; it then waits until two captures agree within the
  noise. It reports `captured_at`, `ms_since_input` and `settled`.

### Agent interface

`phonectl` prints one JSON object per command and exits 1 with `{"error": ...}` on failure.
`screenshot` waits out the input latency and for the screen to stop changing (bounded), so
an agent can act and look without sleeping. See the README for the commands.

## The board

`hardware/circuit.py` is the design: every part with its LCSC number and footprint, and every
net. `tools/generate.py` writes the KiCad schematic and PCB from it, and `tools/board.py`
checks them (ERC, DRC against JLCPCB's limits, schematic parity) and writes the order files.
The board is 32 x 50 mm on two layers: the module at the top with its antenna over bare
board, the camera connector on the left edge, USB-C on the bottom edge, a ground plane on
the back. The generator places the parts and routes them with its own grid router, so the
same circuit always gives the same board. `tests/test_board.py` holds the circuit's camera
and LED pins to the ones the firmware drives.

| Block | Parts | Why |
| --- | --- | --- |
| Processor | ESP32-S3-WROOM-1-N16R8 | native USB device controller, Wi-Fi, a camera interface, 8 MB PSRAM for 2 MP JPEGs |
| USB-C | 16-pin receptacle, 5.1 k on each CC, USBLC6-2 ESD, polyfuse | the phone is the host and supplies 5 V; Rd tells it a device is attached |
| 3.3 V | LDL1117S33R | Wi-Fi peaks of 0.4 A from a phone's 0.5 A port |
| Camera | 24-pin 0.5 mm flex connector, 2.8 V and 1.2 V regulators | the common OV2640 DVP module (2 MP, hardware JPEG); jumpers adapt it to an OV5640 |
| Console | 6-pin UART header, RESET and BOOT buttons | the USB port belongs to the phone |
| Status | LED on GPIO48 | lit when on the network |

Pins: USB D-/D+ on GPIO19/20; camera XCLK 15, SIOD 4, SIOC 5, VSYNC 6, HREF 7, PCLK 13, data
Y9..Y2 on 16, 17, 18, 12, 10, 8, 9, 11 (the ESP32-S3-EYE map, `firmware/main/camera_ov2640.c`).

The phone powers the board, so one cable does everything and nothing else is on the desk.
The cost is that the phone does not charge while the board is plugged in; a later revision
can add a second USB-C port for a charger and negotiate a power role swap.

A 2 MP sensor is enough to read a phone's interface. `tools/camera_study.py` puts phone
screens through the simulated camera at several resolutions and counts the words OCR still
reads, relative to the original display:

| Camera | Screenshot | Words read (Settings, About phone) |
| --- | --- | --- |
| 0.3 MP 640x480 | 225x503 | 23-54%, 30-44% |
| 0.9 MP 1280x720 | 451x1006 | 39-96%, 78-87% |
| 2 MP 1600x1200 (OV2640) | 565x1257 | 85-100%, 83-91% |
| 5 MP 2592x1944 (OV5640) | 917x2038 | 96-100%, 91-96% |

Ranges cover a sharp and a soft lens (blur of 0.8 and 1.5 sensor pixels), with and without
JPEG. The phone fills 80% of the frame's width.

## The simulated board and the virtual rig

Everything that would be hardware is replaced, and the software on both sides is unchanged
and unaware:

| Hardware | Virtual stand-in |
| --- | --- |
| The board | Espressif's QEMU (`-M esp32s3`) running the real firmware image |
| Its USB controller | `sim/qemu/esp32s3_usb_otg.c`: the ESP32-S3's DWC2 controller in device mode, as the firmware's TinyUSB driver uses it. Each packet on the bus is a frame on a socket |
| Its camera | `sim/qemu/esp32s3_simcam.c`: a register interface that hands the firmware the current JPEG of the scene |
| Wi-Fi | QEMU's OpenCores Ethernet with the device's port forwarded to localhost |
| USB cable | `rig/cable.py`: the board's socket on one side, a USB/IP server on the other; this host's `vhci-hcd` attaches it and the emulator's `-usb-passthrough` hands it to the phone |
| Phone | Google's Android emulator, headless (Android 16, Google APIs) |
| The scene the camera sees | `rig/camera.py`: the emulator's composited display (secure windows and cursor included) on a desk at an angle: bezel, keystone, lens blur, sensor noise; 1600x1200 |

The firmware differs between the two boards in two files of each kind: `camera_sim.c` or
`camera_ov2640.c`, `net_sim.c` or `net_wifi.c`. USB, the TCP service, input pacing and
settings are the same code, and the USB path is the same down to the controller's registers.

The emulated phone enumerates the board with its own kernel's xHCI, `usbhid` and
`hid-multitouch` drivers, the same path a physical phone takes. The host's own HID driver is
detached from the board the moment it attaches, so the board never types into the laptop.
`adb` is the tests' oracle and feeds the scene; the device and `phonectl` never use it.

Taking the emulator's display stops the emulator for some 60 ms, and it then hands the phone
the board's reports late and in a burst: a swipe becomes a jump. So the scene is not refreshed
while reports are flowing (`rig/cable.py` marks each one), and the phone sees them 8 ms apart
as a real one does.

## What the rig proves and what it cannot

Proven by `bin/check` against the emulated phone (16 of its 24 tests, adb and the emulator's own
display as oracle): enumeration of all four interfaces and that their descriptors match the
reports sent, screenshot fidelity, tap accuracy, every printable ASCII character, key
combinations, home/back/recents/power/volume/media keys, long press, swipe, two-finger
pinch, mouse pointing, click and wheel, the reported cursor position, that a screenshot
never predates an input, unlocking with a PIN, a password and a pattern, and that the PIN
entry screen is visible to the camera.

Needs the real board and a real phone:

1. **The board itself.** ERC and DRC pass; whether it works is known when one is assembled.
   Power is the risk: Wi-Fi transmit peaks on a phone's USB port. Before ordering, check the
   camera connector against the actual module's flex (contact side and which end is pin 1;
   the footprint is the bottom-contact sibling's, assumed identical) and the part rotations
   in JLCPCB's placement preview.
2. **The optics.** Focus at 10 to 20 cm (fixed-focus modules are set for infinity and need
   their lens turned), glare, moire against the pixel grid, exposure of a bright screen in a
   dark room, lens distortion. None is simulated. The mount that holds the camera over the
   phone is not designed yet.
3. **The real camera driver and Wi-Fi.** `camera_ov2640.c` and `net_wifi.c` compile and are
   small, but have never run.
4. **Pointer speed.** The mouse gain depends on the phone's pointer-speed setting;
   `calibrate pointer` measures it, on a screen that does not react to hovering.
5. **Sleep.** A phone may suspend USB when asleep; whether the power key wakes it over USB
   varies by phone. In the rig it does.
6. **Other Android builds.** The rig's phone is stock Android 16; MIUI, One UI and others
   may treat an external touchscreen or mouse differently. One has been tried, through the
   debugging cable below: a Redmi Note 12 Pro+ (Android 14, MIUI, kernel 4.19). Its kernel is
   built without `hid-multitouch`, so the touch interface gets the generic driver, which does
   not mark it as a screen, and Android treats it as a touchpad: touches do nothing useful. A
   single-touch pen digitizer fares the same there. Keyboard, consumer keys and mouse work,
   on the lock screen too: a key wakes the phone and a mouse drag swipes to the pattern pad.
   On such phones the mouse is the pointing device.

A real phone can stand in for the virtual one before the board exists (`rig up --phone real`).
This host has no USB port that can be a device, so `rig/uhid.py` is the cable: it is the USB
host the simulated board expects, and it creates each of the board's interfaces as a HID
device in the phone's kernel (uhid) over adb, with the interface's own descriptor and every
report the firmware sends. `rig/webcam.py` puts a webcam's picture in front of the board's
camera. This exercises the phone's HID drivers and input stack and real optics; it does not
exercise USB itself, needs debugging on, and cannot reach a phone before its first unlock
after boot.

Known limits by design: `type` sends keys of a US layout, so text outside printable ASCII
needs the phone's own input method (or pasting); the screenshot has the resolution the
camera gives the screen (about 565x1257 from 2 MP), not the panel's.

Measured in the rig: a picture takes about 60 ms, 20 input reports 0.11 s.

## Stages

1. Design: this document.
2. The whole device on a simulated board: firmware, host software, tests. Done.
3. The board: schematic, layout, order files. Done; not yet ordered.
4. Assembled board against a real phone: the list above.
