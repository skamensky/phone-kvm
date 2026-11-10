"""The reports the device's four USB HID interfaces carry. The interfaces and their report
descriptors are the firmware's (firmware/main/usb.c); none uses report IDs.

  keyboard  boot-protocol keyboard, 8-byte reports
  touch     multi-touch touchscreen digitizer; Android shows no cursor and takes taps directly
  mouse     relative mouse with wheel; Android shows a cursor
  consumer  one consumer-page usage: Home, Back, app switcher, power, volume, media

Touch coordinates are 0..TOUCH_MAX on both axes, spanning the display Android binds the
touchscreen to.
"""
import struct

TOUCH_MAX = 32767
CONTACTS = 5

# Each interface's number on the device and the length of its input report.
INTERFACES = {"keyboard": (0, 8), "touch": (1, 6 * CONTACTS + 1), "mouse": (2, 6), "consumer": (3, 2)}

# Consumer-page usages and the Android key each becomes.
CONSUMER = {
    "home": 0x223,         # AC Home -> HOME
    "back": 0x224,         # AC Back -> BACK
    "recents": 0x1A2,      # AL Select Task -> APP_SWITCH
    "power": 0x30,         # Power -> POWER
    "volume-up": 0xE9,
    "volume-down": 0xEA,
    "mute": 0xE2,
    "play-pause": 0xCD,
    "next-track": 0xB5,
    "previous-track": 0xB6,
    "search": 0x221,       # AC Search -> SEARCH
    "menu": 0x40,
}


def keyboard_report(modifiers=0, keys=()):
    if len(keys) > 6:
        raise ValueError("a boot keyboard reports at most six keys")
    return bytes([modifiers, 0, *keys, *([0] * (6 - len(keys)))])


def touch_report(contacts):
    """contacts: [(contact id, down, x, y)], x and y in 0..TOUCH_MAX.

    A lifted finger is reported once more with down=False; fingers absent from a report are
    unchanged only if the host tracks them, so always list every finger that is down.
    """
    if len(contacts) > CONTACTS:
        raise ValueError(f"at most {CONTACTS} contacts")
    out = b""
    for cid, down, x, y in contacts:
        if not (0 <= x <= TOUCH_MAX and 0 <= y <= TOUCH_MAX):
            raise ValueError(f"touch point ({x}, {y}) is outside 0..{TOUCH_MAX}")
        out += struct.pack("<BBHH", 1 if down else 0, cid, x, y)
    out += bytes(6 * (CONTACTS - len(contacts)))
    return out + bytes([len(contacts)])


def mouse_report(buttons=0, dx=0, dy=0, wheel=0):
    return struct.pack("<Bhhb", buttons, dx, dy, wheel)


def consumer_report(usage=0):
    return struct.pack("<H", usage)
