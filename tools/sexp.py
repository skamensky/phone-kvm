"""A minimal reader and writer for KiCad's S-expression files."""
import re

TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


class Str(str):
    """A string that was quoted in the source and must be quoted when written."""


def parse(text):
    stack, cur = [], []
    for m in TOKEN.finditer(text):
        if m[1]:
            stack.append(cur)
            cur = []
        elif m[2]:
            done, cur = cur, stack.pop()
            cur.append(done)
        elif m[3] is not None:
            cur.append(Str(m[3].replace('\\"', '"').replace("\\\\", "\\")))
        elif m[4]:
            cur.append(m[4])
    return cur[0]


def dump(node, indent=0):
    if isinstance(node, list):
        if not any(isinstance(x, list) for x in node):
            return "(" + " ".join(dump(x) for x in node) + ")"
        pad = "\t" * (indent + 1)
        parts = [dump(node[0])] if node else []
        for x in node[1:]:
            parts.append("\n" + pad + dump(x, indent + 1) if isinstance(x, list) else " " + dump(x))
        return "(" + parts[0] + "".join(parts[1:]) + "\n" + "\t" * indent + ")"
    if isinstance(node, Str):
        return '"' + node.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(node, float):
        return f"{node:.4f}".rstrip("0").rstrip(".")
    return str(node)


def find(node, key):
    return next((x for x in node if isinstance(x, list) and x and x[0] == key), None)


def find_all(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]
