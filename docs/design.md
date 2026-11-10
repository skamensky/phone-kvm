# phone-kvm design

## Goal

Let an agent use a real Android phone exactly as a person does: look at the screen, touch it,
type, press the hardware keys, use a mouse. Nothing is installed on the phone, no developer
mode, no adb. The phone sees a USB keyboard, touchscreen and mouse and an external display,
all of which it supports out of the box. Whatever a person can do with fingers and eyes,
the agent can do, including unlocking with a PIN, password or pattern.

## Shape

```
            USB (HID gadget)                    phone's USB-C port
  device  ───────────────────────────────►  phone
 (Linux)  ◄───────────────────────────────
            HDMI (phone mirrors its screen)
     ▲
     │ phonectl (CLI, JSON)        over SSH or locally
   agent
```

The device is a Linux computer with a USB device controller. It runs one Python package,
`phone_kvm`, with three parts:

| Part | File | Job |
| --- | --- | --- |
| Gadget | `phone_kvm/gadget.py`, `hid.py` | Presents four USB HID interfaces through configfs (`libcomposite`, `f_hid`) |
| Screen | `phone_kvm/video.py` | Reads frames from any V4L2 capture device and cuts the phone's screen out of an HDMI frame or a camera's view (`phone_kvm/camera.py`) |
| Control | `phone_kvm/phone.py`, `cli.py` | `phonectl`: screenshot, tap, swipe, pinch, type, key, mouse |

### USB interfaces

| Interface | Reports | Android sees | Used for |
| --- | --- | --- | --- |
| keyboard | boot keyboard, 8 bytes | external keyboard | text, shortcuts, PIN and password entry |
| touch | 5-contact digitizer, absolute 0..32767, 31 bytes | direct touchscreen (no cursor) | tap, long press, swipe, multi-point path, pinch |
| mouse | 3 buttons, relative X/Y, wheel, 4 bytes | mouse with a cursor | hover, right click, wheel |
| consumer | one 16-bit usage, 2 bytes | system keys | home, back, recents, power, volume, media |

Touch is the primary pointer: it is absolute, so a screenshot pixel maps straight to a touch
position with no feedback loop. The mouse exists because some things only a mouse does
(hover, right click, wheel).

### Coordinates

Every position `phonectl` takes is a pixel of the image `phonectl screenshot` writes. A
screenshot pixel is scaled to the digitizer's 0..32767 range, which Android maps onto the
whole display.

### Views

`video.py` cuts the phone's screen out of a capture frame in one of two ways, chosen at
`setup`:

- **hdmi**: a phone mirrors a portrait screen into a landscape frame with black bars; the
  screen is the box that is not black.
- **camera**: a camera looks at the phone. `camera.py` finds the lit screen's four corners
  (threshold, largest lit region, a line fitted to each edge at sub-pixel precision) and
  maps that quadrilateral back to an upright rectangle. Four frames are averaged, which
  halves sensor noise. A camera sees what a person sees, including the secure windows
  Android blanks on HDMI. The camera and phone must not move after calibration, and the
  screen must be light and reach all four edges while it is found: a dark wallpaper cannot
  be told from the bezel.

### Calibration: where, pointer, when

Three stored measurements tie the image an agent sees to the inputs it sends
(`phonectl calibrate`, shown by `status`):

- **Where** (`geometry`): the screen's box or corners in the frame, and the screenshot size.
- **Pointer** (`pixels_per_count`, `offset`): the mouse is relative. Android moves the cursor
  a fixed distance per count while a report carries at most 2 counts, and accelerates above
  that, so all movement is sent in such reports. Calibration runs the cursor into the
  top-left corner, steps it out along a diagonal, finds its tip at each stop by differencing
  screenshots (ignoring areas that change by themselves, such as the clock) and fits a line.
  The session then tracks the cursor from the counts it sends; `screenshot` reports it as
  `pointer`, and it is unknown (null) until `mouse to` or calibration has placed it.
- **When** (`latency_ms`, `noise`): the time from an input to the first frame that shows all
  of it, measured with cursor jumps, and how much two frames of a still screen differ.
  `screenshot` does not capture before the last input's latency has passed, so an image
  never predates the input it follows; it then waits until two captures agree within the
  noise. It reports `captured_at`, `ms_since_input` and `settled`.

### Agent interface

`phonectl` prints one JSON object per command and exits 1 with `{"error": ...}` on failure.
`screenshot` waits out the input latency and for the screen to stop changing (bounded), so
an agent can act and look without sleeping. See the README for the commands.

## Virtual rig

Everything that would be hardware is replaced, and the device code is unchanged and unaware:

| Hardware | Virtual stand-in |
| --- | --- |
| Phone | Google's Android emulator, headless (Android 16, Google APIs) |
| Device's USB controller | the kernel's `usbip-vudc` virtual device controller |
| USB cable | USB/IP on localhost (`usbipd --device`, `vhci-hcd`), then the emulator's `-usb-passthrough` |
| HDMI out, cable and capture dongle | the emulator's framebuffer written as a 1920x1080 stream to a `v4l2loopback` device |
| Camera on a stand | the emulator's composited display (secure windows and cursor included) placed in a 1600x1200 scene: bezel, desk, keystone, lens blur, sensor noise; a second `v4l2loopback` device |

The emulated phone enumerates the gadget with its own kernel's xHCI, `usbhid` and
`hid-multitouch` drivers, the same path a physical phone takes. `adb` carries the
framebuffer for the virtual HDMI cable and is the tests' oracle; the device never uses it.

The host's own HID driver is detached from the gadget the moment it attaches, so the device
never types into the laptop.

## Physical device (not yet built)

Proposed, to be validated on hardware:

- Raspberry Pi 4 or 5. Its USB-C port is the device-mode port (`dwc2`); power it through the
  GPIO header or PoE so that port is free for data.
- A USB-C dock on the phone with HDMI out and a USB-A port. Dock USB-A to the Pi's USB-C
  (gadget). Dock HDMI to a UVC HDMI capture dongle in one of the Pi's USB-A ports. Dock
  power input charges the phone.
- The agent reaches `phonectl` over SSH.
- Or, instead of the dock's HDMI and the dongle: a camera on a stand over the phone. This
  works with any Android phone (no DisplayPort alternate mode needed) and shows secure
  windows. `tools/camera_study.py` puts phone screens through the simulated camera at several
  resolutions and counts the words OCR still reads, relative to the original display:

  | Camera | Screenshot | Words read (Settings, About phone) |
  | --- | --- | --- |
  | HDMI capture, for reference | 486x1080 | 100%, 96% |
  | 0.3 MP 640x480 | 225x503 | 23-54%, 30-44% |
  | 0.9 MP 1280x720 | 451x1006 | 39-96%, 78-87% |
  | 2 MP 1600x1200 (OV2640) | 565x1257 | 85-100%, 83-91% |
  | 2 MP 1920x1080 (webcam) | 679x1509 | 96-100%, 87% |
  | 5 MP 2592x1944 (OV5647) | 917x2038 | 96-100%, 91-96% |

  Ranges cover a sharp and a soft lens (blur of 0.8 and 1.5 sensor pixels), with and without
  JPEG. The phone fills 80% of the frame's width. 2 MP is enough to read a phone's interface
  if the lens is in focus; 720p depends on the lens; VGA is not enough. Not simulated, and
  decisive on real hardware: focus at 15 to 25 cm (fixed-focus modules are set for
  infinity and need their lens turned), glare, moire against the pixel grid, exposure of a
  bright screen in a dark room, lens distortion.

Software changes for hardware: none expected. `gadget up` picks the only controller,
`phonectl setup --video /dev/videoN` picks the dongle.

## What the rig proves and what it cannot

Proven by `bin/check` against the emulated phone, through the HDMI view and again through
the camera view (39 tests, adb and the emulator's own display as oracle): enumeration of
all four interfaces, screenshot fidelity, tap accuracy, every printable ASCII character,
key combinations, home/back/recents/power/volume/media keys, long press, swipe, two-finger
pinch, mouse pointing, click and wheel, the reported cursor position, that a screenshot
never predates an input, unlocking with a PIN, a password and a pattern, and that the PIN
entry screen is black over HDMI and visible to the camera.

A real 1080p webcam (a laptop's integrated UVC camera, MJPEG) works as the capture device
unchanged: `setup --view camera --size 1920x1080 --input-format mjpeg`, about 30 frames per
second, still-frame noise 10 to 13 in a dim room against 9 in the simulation. It has not yet
been pointed at a phone.

Needs a real phone:

1. **Video out.** The phone must support DisplayPort alternate mode on USB-C (Pixel 8 and
   later, Samsung flagships, most others do not). Without it there is no screen.
2. **Secure windows are black.** Android blanks secure layers (the PIN/password/pattern
   entry screen, banking apps, DRM video) on a display without HDCP, and capture dongles
   have none. The rig reproduces this: its screenshots of the unlock screen are black, and
   the unlock tests pass anyway because typing a PIN or password needs no sight and pattern
   dots sit at fixed positions for a given phone. A camera view shows them (the rig proves
   this too); an HDCP-terminating splitter in front of the dongle is the other answer.
3. **Which display the touchscreen binds to.** With no external display Android binds the
   USB touchscreen to the phone's own screen, which is what we want. With HDMI attached some
   builds may bind an external touchscreen to the external display. In mirror mode the
   mapping is the same picture, but letterboxing could offset it; if so, `_point` needs the
   frame geometry, which `video.py` already has.
4. **Mirroring versus desktop mode.** Samsung DeX and Android's desktop mode put a different
   desktop on HDMI. The phone must be set to mirror.
5. **Capture quality.** Dongles output MJPEG or YUV with colour conversion; black-bar
   detection uses a threshold for that reason, and it is untested against a real dongle.
6. **Pointer speed.** The mouse gain depends on the phone's pointer-speed setting;
   `calibrate pointer` measures it, on a screen that does not react to hovering.
7. **Sleep.** A phone may suspend USB when asleep; whether the power or a keyboard key wakes
   it over USB varies by phone. In the rig the power key wakes it.

Known limits by design: `type` sends keys of a US layout, so text outside printable ASCII
needs the phone's own input method (or pasting); the screenshot is the HDMI frame's
resolution (1080 lines by default; `bin/rig up --hdmi 3840x2160` for more), not the panel's.

Measured in the rig: input to first visible change 0.3 to 1.3 s, most of it the virtual
HDMI path (about 3 frames per second from the emulator); a hardware dongle is 30 to 60.

## Stages

1. Design: this document.
2. Proof: gadget, virtual cables and emulator talking to each other; one key and one tap
   observed inside Android. Done.
3. Full device: all four interfaces, screen capture, calibrated coordinates, `phonectl`,
   tests. Done in the rig. Hardware validation of the list above remains.
