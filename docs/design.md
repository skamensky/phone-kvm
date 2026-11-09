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
| Screen | `phone_kvm/video.py` | Reads frames from any V4L2 capture device and crops the phone's screen out of the HDMI frame |
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
phone mirrors a portrait screen into a landscape HDMI frame with black bars; `video.py`
finds the picture inside the frame once and stores it (`calibrate` repeats it after a
rotation). A screenshot pixel is scaled to the digitizer's 0..32767 range, which Android
maps onto the whole display.

The mouse is relative and Android accelerates it. `mouse to X Y` runs the cursor into the
top-left corner, then moves out in reports of at most 2 counts, where Android applies no
acceleration, by a gain measured once by watching the cursor on the screen
(`mouse calibrate`).

### Agent interface

`phonectl` prints one JSON object per command and exits 1 with `{"error": ...}` on failure.
`screenshot` waits for the screen to stop changing (bounded), so an agent can act and look
without sleeping. See the README for the commands.

## Virtual rig

Everything that would be hardware is replaced, and the device code is unchanged and unaware:

| Hardware | Virtual stand-in |
| --- | --- |
| Phone | Google's Android emulator, headless (Android 16, Google APIs) |
| Device's USB controller | the kernel's `usbip-vudc` virtual device controller |
| USB cable | USB/IP on localhost (`usbipd --device`, `vhci-hcd`), then the emulator's `-usb-passthrough` |
| HDMI out, cable and capture dongle | the emulator's framebuffer written as a 1920x1080 stream to a `v4l2loopback` device |

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

Software changes for hardware: none expected. `gadget up` picks the only controller,
`phonectl setup --video /dev/videoN` picks the dongle.

## What the rig proves and what it cannot

Proven by `bin/check` against the emulated phone (22 tests, adb as oracle): enumeration of
all four interfaces, screenshot fidelity, tap accuracy, every printable ASCII character,
key combinations, home/back/recents/power/volume/media keys, long press, swipe, two-finger
pinch, mouse pointing, click and wheel, and unlocking with a PIN, a password and a pattern.

Needs a real phone:

1. **Video out.** The phone must support DisplayPort alternate mode on USB-C (Pixel 8 and
   later, Samsung flagships, most others do not). Without it there is no screen.
2. **Secure windows are black.** Android blanks secure layers (the PIN/password/pattern
   entry screen, banking apps, DRM video) on a display without HDCP, and capture dongles
   have none. The rig reproduces this: its screenshots of the unlock screen are black, and
   the unlock tests pass anyway because typing a PIN or password needs no sight and pattern
   dots sit at fixed positions for a given phone. An HDCP-terminating splitter in front of
   the dongle is the usual hardware answer where sight is needed.
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
   `mouse calibrate` measures it, on a screen that does not react to hovering.
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
