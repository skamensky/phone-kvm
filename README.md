# phone-kvm

Gives an agent a real Android phone to use the way a person does: a small Linux device
plugs into the phone as a USB keyboard, touchscreen and mouse, and watches the phone's
screen over HDMI. Nothing is installed on the phone. Design, hardware plan and open
hardware questions: [docs/design.md](docs/design.md).

The hardware is not built yet. The same device software runs here against a virtual phone
(the Android emulator) over a virtual USB cable and a virtual HDMI cable.

## Virtual rig

Needs Linux with KVM, passwordless `sudo`, Nix, and the stock kernel modules `libcomposite`,
`usbip-vudc`, `vhci-hcd` and `v4l2loopback`, plus `usbip`/`usbipd` (Ubuntu:
`linux-tools-generic`, `v4l2loopback-dkms`). The flake accepts the Android SDK licence.

    bin/rig up        # gadget, cables, emulator; about 40 s; prints status with "ready": true
    bin/rig status
    bin/rig down
    bin/check         # device logic, and the device against the phone when the rig is up

## Using the phone

Every command prints one JSON object. Positions are pixels of the screenshot.

    bin/phonectl screenshot shot.png        # waits for the screen to settle; --settle-ms 0 to not
    bin/phonectl tap 243 990                # --hold-ms 800 for a long press
    bin/phonectl swipe 243 800 243 300      # more points trace a path: an unlock pattern
    bin/phonectl pinch 243 500 --start 80 --end 300
    bin/phonectl type 'Hello, World!'
    bin/phonectl key enter                  # home back recents power volume-up ... ctrl+a meta+n
    bin/phonectl mouse to 187 715           # also: move DX DY, click [right], scroll N, calibrate
    bin/phonectl calibrate                  # after the phone rotates
    bin/phonectl status

The loop for an agent is: `screenshot`, read the image, act, `screenshot`.

Unlocking: `key power` wakes the phone, a swipe up opens the entry screen, then `type` the
PIN or password and `key enter`, or `swipe` through the pattern's dots. The entry screen is
a secure window and shows black in screenshots (see the design document).

`bin/*` run inside the project's Nix shell; set `PHONE_KVM_SHELL=1` when already in it
(`nix develop`) to skip the start-up cost.

## On real hardware

    sudo python -m phone_kvm.gadget up --owner $UID
    python -m phone_kvm.cli setup --video /dev/video0
