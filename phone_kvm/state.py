"""Small JSON records the device keeps between commands (configuration, screen geometry)."""
import json
import os
from pathlib import Path

DIR = Path(os.environ.get("PHONE_KVM_STATE") or Path.home() / ".local/state/phone-kvm")


def load(name):
    path = DIR / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save(name, value):
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"{name}.json").write_text(json.dumps(value, indent=1) + "\n")
