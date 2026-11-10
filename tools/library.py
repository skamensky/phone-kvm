"""Reads KiCad library symbols (flattening `extends`) and their pins, and finds the libraries."""
import copy
import os
import re
import shutil
from pathlib import Path

from sexp import Str, find, find_all, parse

_libs = {}


def library_dir(kind):
    """KiCad's symbol or footprint library directory: $KICAD10_{SYMBOL,FOOTPRINT}_DIR, or the
    default the kicad-cli on PATH was built with (a Nix kicad-cli is a wrapper that sets it)."""
    var = f"KICAD10_{kind}_DIR"
    if os.environ.get(var):
        return Path(os.environ[var])
    cli = shutil.which("kicad-cli")
    if cli:
        wrapper = Path(cli).resolve().read_bytes()[:20000].decode(errors="ignore")
        m = re.search(var + r"=\$\{" + var + r"-'([^']+)'\}", wrapper)
        if m:
            return Path(m[1])
        share = Path(cli).resolve().parent.parent / "share/kicad" / {"SYMBOL": "symbols", "FOOTPRINT": "footprints"}[kind]
        if share.is_dir():
            return share
    raise SystemExit(f"set {var} to KiCad's {kind.lower()} library directory")


def _lib(name):
    if name not in _libs:
        text = (library_dir("SYMBOL") / f"{name}.kicad_sym").read_text()
        _libs[name] = {s[1]: s for s in find_all(parse(text), "symbol")}
    return _libs[name]


def symbol(lib_id):
    """The symbol as a schematic's lib_symbols stores it: flattened and named lib:name."""
    lib, name = lib_id.split(":")
    sym = copy.deepcopy(_lib(lib)[name])
    parent = find(sym, "extends")
    if parent:
        base = symbol(f"{lib}:{parent[1]}")
        props = {p[1]: p for p in find_all(sym, "property")}
        out = [x for x in base if not (isinstance(x, list) and x[0] == "property")]
        merged = [props.get(p[1], p) for p in find_all(base, "property")]
        merged += [p for k, p in props.items() if k not in {q[1] for q in merged}]
        out[2:2] = merged
        for unit in find_all(out, "symbol"):
            unit[1] = Str(unit[1].replace(base[1].split(":")[-1], name, 1))
        sym = out
    sym[1] = Str(lib_id)
    return sym


_pins = {}


def pins(lib_id):
    """[(number, name, x, y, type)] in symbol coordinates (y up)."""
    if lib_id not in _pins:
        out = []
        for unit in find_all(symbol(lib_id), "symbol"):
            for pin in find_all(unit, "pin"):
                at = find(pin, "at")
                out.append((find(pin, "number")[1], find(pin, "name")[1], float(at[1]), float(at[2]), pin[1]))
        _pins[lib_id] = out
    return _pins[lib_id]
