# phone-kvm

Gives an agent a real Android phone to use the way a person does: a small Linux device
plugs into the phone as a USB keyboard, touchscreen and mouse, and watches the phone's
screen over HDMI or with a camera pointed at it. Nothing is installed on the phone. Design,
hardware plan and open hardware questions: [docs/design.md](docs/design.md).

The hardware is not built yet. The same device software runs here against a virtual phone
(the Android emulator) over a virtual USB cable, a virtual HDMI cable and a virtual camera.

## Virtual rig

Needs Linux with KVM, passwordless `sudo`, Nix, and the stock kernel modules `libcomposite`,
`usbip-vudc`, `vhci-hcd` and `v4l2loopback`, plus `usbip`/`usbipd` (Ubuntu:
`linux-tools-generic`, `v4l2loopback-dkms`). The flake accepts the Android SDK licence.

    bin/rig up        # gadget, cables, emulator; about 40 s; prints status with "ready": true
    bin/rig status
    bin/rig down
    bin/check         # device logic, and the device against the phone through both views
                      # when the rig is up (about 7 minutes)

The rig offers both views of the same phone: `/dev/video42` is the HDMI capture (the default
after `rig up`) and `/dev/video43` is a 1600x1200 camera looking at the phone at an angle,
with lens blur and sensor noise. To use the camera:

    bin/phonectl setup --video /dev/video43 --view camera --rotate 90
    bin/phonectl key meta+i && bin/phonectl calibrate screen    # a light screen: Settings
    bin/phonectl key home && bin/phonectl calibrate pointer

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
    bin/phonectl calibrate                  # after setup, and after the phone rotates or moves
    bin/phonectl frame raw.png              # the whole capture frame, for aiming a camera
    bin/phonectl status

The loop for an agent is: `screenshot`, read the image, act, `screenshot`.

`screenshot` also reports `captured_at`, `ms_since_input`, `settled` and `pointer`: where the
mouse cursor is in that image, or null when it is not known. `calibrate` measures what ties
the image to the inputs: where the screen is in the frame, how far the cursor moves per
mouse count, how long an input takes to show in the video, and the video's noise.
`calibrate screen` and `calibrate pointer` do the halves separately. Through a camera the
screen is found as a lit rectangle, so `calibrate screen` needs a light screen that reaches
all four edges; `calibrate pointer` needs a screen that does not react to hovering (the
home screen away from icons).

Unlocking: `key power` wakes the phone, a swipe up opens the entry screen, then `type` the
PIN or password and `key enter`, or `swipe` through the pattern's dots. The entry screen is
a secure window: black over HDMI, visible to a camera (see the design document).

`bin/*` run inside the project's Nix shell; set `PHONE_KVM_SHELL=1` when already in it
(`nix develop`) to skip the start-up cost.

## On real hardware

    sudo python -m phone_kvm.gadget up --owner $UID
    python -m phone_kvm.cli setup --video /dev/video0     # an HDMI capture dongle
    python -m phone_kvm.cli setup --video /dev/video0 --view camera --rotate 90 \
        --size 1920x1080 --input-format mjpeg             # or a camera; then calibrate

`--rotate` is how far clockwise the phone appears turned in the camera's frame. Aim with
`frame`: the phone's screen should fill most of the frame, on a dark surface, without glare.
