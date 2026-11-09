"""Names and characters to USB keyboard usages, for a US layout (Android's default for a
keyboard it does not recognise)."""

CTRL, SHIFT, ALT, META = 0x01, 0x02, 0x04, 0x08
MODIFIERS = {"ctrl": CTRL, "shift": SHIFT, "alt": ALT, "meta": META}

KEYS = {
    "enter": 0x28, "esc": 0x29, "backspace": 0x2A, "tab": 0x2B, "space": 0x2C,
    "delete": 0x4C, "insert": 0x49, "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52,
    "page-up": 0x4B, "page-down": 0x4E, "line-start": 0x4A, "line-end": 0x4D,
    **{f"f{n}": 0x39 + n for n in range(1, 13)},
}

# character: (usage, needs shift)
CHARS = {"\n": (0x28, False), "\t": (0x2B, False), " ": (0x2C, False)}
for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    CHARS[c] = (0x04 + i, False)
    CHARS[c.upper()] = (0x04 + i, True)
for i, (plain, shifted) in enumerate(zip("1234567890", "!@#$%^&*()")):
    CHARS[plain] = (0x1E + i, False)
    CHARS[shifted] = (0x1E + i, True)
for usage, (plain, shifted) in zip(
    (0x2D, 0x2E, 0x2F, 0x30, 0x31, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38),
    ("-_", "=+", "[{", "]}", "\\|", ";:", "'\"", "`~", ",<", ".>", "/?"),
):
    CHARS[plain] = (usage, False)
    CHARS[shifted] = (usage, True)


def combo(text):
    """'ctrl+shift+a' -> (modifier bits, usage)."""
    *mods, key = text.lower().split("+")
    bits = 0
    for m in mods:
        if m not in MODIFIERS:
            raise ValueError(f"unknown modifier {m!r}; known: {', '.join(MODIFIERS)}")
        bits |= MODIFIERS[m]
    if key in MODIFIERS:
        return bits | MODIFIERS[key], None
    if key in KEYS:
        return bits, KEYS[key]
    if key in CHARS:
        usage, shift = CHARS[key]
        return bits | (SHIFT if shift else 0), usage
    raise ValueError(f"unknown key {key!r}")
