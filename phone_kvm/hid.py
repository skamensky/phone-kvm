"""The device's USB HID interfaces: report descriptors and the reports they carry.

Four interfaces, each with its own descriptor and no report IDs:

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

KEYBOARD_DESC = bytes([
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01,              # Generic Desktop / Keyboard, Application
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7,              # modifiers LeftCtrl..RightGUI
    0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x08, 0x81, 0x03,              # reserved byte
    0x95, 0x05, 0x75, 0x01, 0x05, 0x08, 0x19, 0x01, 0x29, 0x05, 0x91, 0x02,  # LEDs (output)
    0x95, 0x01, 0x75, 0x03, 0x91, 0x03,
    0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x25, 0x65,  # six key slots
    0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x81, 0x00,
    0xC0,
])

_FINGER = bytes([
    0x09, 0x22, 0xA1, 0x02,                          # Finger, Logical
    0x09, 0x42, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x01, 0x81, 0x02,  # Tip Switch
    0x95, 0x07, 0x81, 0x03,                          # padding
    0x09, 0x51, 0x25, CONTACTS - 1, 0x75, 0x08, 0x95, 0x01, 0x81, 0x02,      # Contact ID
    0x05, 0x01, 0x26, TOUCH_MAX & 0xFF, TOUCH_MAX >> 8, 0x75, 0x10, 0x95, 0x01,
    0x09, 0x30, 0x81, 0x02,                          # X
    0x09, 0x31, 0x81, 0x02,                          # Y
    0x05, 0x0D,
    0xC0,
])

TOUCH_DESC = (
    bytes([0x05, 0x0D, 0x09, 0x04, 0xA1, 0x01])      # Digitizer / Touch Screen, Application
    + _FINGER * CONTACTS
    + bytes([
        0x09, 0x54, 0x25, CONTACTS, 0x75, 0x08, 0x95, 0x01, 0x81, 0x02,  # Contact Count
        0x09, 0x55, 0x25, CONTACTS, 0xB1, 0x02,      # Contact Count Maximum (feature)
        0xC0,
    ])
)

MOUSE_DESC = bytes([
    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x09, 0x01, 0xA1, 0x00,  # Mouse / Pointer
    0x05, 0x09, 0x19, 0x01, 0x29, 0x03, 0x15, 0x00, 0x25, 0x01,
    0x95, 0x03, 0x75, 0x01, 0x81, 0x02,              # three buttons
    0x95, 0x01, 0x75, 0x05, 0x81, 0x03,
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x38,  # X, Y, wheel
    0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x03, 0x81, 0x06,
    0xC0, 0xC0,
])

CONSUMER_DESC = bytes([
    0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01,              # Consumer Control
    0x15, 0x00, 0x26, 0xFF, 0x03, 0x19, 0x00, 0x2A, 0xFF, 0x03,
    0x75, 0x10, 0x95, 0x01, 0x81, 0x00,              # one 16-bit usage
    0xC0,
])

# name: (descriptor, report length, bInterfaceSubClass, bInterfaceProtocol)
INTERFACES = {
    "keyboard": (KEYBOARD_DESC, 8, 1, 1),
    "touch": (TOUCH_DESC, 6 * CONTACTS + 1, 0, 0),
    "mouse": (MOUSE_DESC, 4, 1, 2),
    "consumer": (CONSUMER_DESC, 2, 0, 0),
}

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
    return struct.pack("<Bbbb", buttons, dx, dy, wheel)


def consumer_report(usage=0):
    return struct.pack("<H", usage)
