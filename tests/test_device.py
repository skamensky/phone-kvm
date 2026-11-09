"""The device's own logic, with no phone attached: descriptors, reports and the key map."""
import pytest

from phone_kvm import hid, keymap


def input_bits(descriptor):
    """Parse a HID report descriptor's short items; return the input report's length in bits."""
    bits = size = count = 0
    i = 0
    while i < len(descriptor):
        prefix = descriptor[i]
        length = (0, 1, 2, 4)[prefix & 3]
        value = int.from_bytes(descriptor[i + 1:i + 1 + length], "little")
        tag = prefix & 0xFC
        assert tag != 0x84, "report IDs would change every report's layout"
        if tag == 0x74:
            size = value
        elif tag == 0x94:
            count = value
        elif tag == 0x80:
            bits += size * count
        i += 1 + length
    assert i == len(descriptor), "descriptor ends inside an item"
    return bits


@pytest.mark.parametrize("name", hid.INTERFACES)
def test_descriptor_describes_the_report_the_gadget_declares(name):
    descriptor, length, _, _ = hid.INTERFACES[name]
    assert input_bits(descriptor) == length * 8


def test_reports_have_the_declared_length():
    assert len(hid.keyboard_report(2, [4])) == hid.INTERFACES["keyboard"][1]
    assert len(hid.touch_report([(0, True, 1, 2)])) == hid.INTERFACES["touch"][1]
    assert len(hid.mouse_report(1, -5, 5, 1)) == hid.INTERFACES["mouse"][1]
    assert len(hid.consumer_report(0x223)) == hid.INTERFACES["consumer"][1]


def test_touch_report_layout():
    report = hid.touch_report([(0, True, 0x1234, 0x5678), (1, False, 1, 2)])
    assert report[:6] == bytes([1, 0, 0x34, 0x12, 0x78, 0x56])
    assert report[6:12] == bytes([0, 1, 1, 0, 2, 0])
    assert report[12:30] == bytes(18) and report[30] == 2


def test_touch_report_rejects_what_the_descriptor_cannot_carry():
    with pytest.raises(ValueError):
        hid.touch_report([(0, True, hid.TOUCH_MAX + 1, 0)])
    with pytest.raises(ValueError):
        hid.touch_report([(i, True, 0, 0) for i in range(hid.CONTACTS + 1)])


def test_every_printable_ascii_character_can_be_typed():
    assert {chr(c) for c in range(32, 127)} <= set(keymap.CHARS)
    assert keymap.CHARS["a"] == (0x04, False) and keymap.CHARS["A"] == (0x04, True)
    assert keymap.CHARS["1"] == (0x1E, False) and keymap.CHARS["!"] == (0x1E, True)


def test_key_combinations():
    assert keymap.combo("ctrl+shift+a") == (keymap.CTRL | keymap.SHIFT, 0x04)
    assert keymap.combo("enter") == (0, 0x28)
    with pytest.raises(ValueError):
        keymap.combo("ctrl+nonsense")
