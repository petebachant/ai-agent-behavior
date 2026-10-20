"""Reference implementation of devkit.ini, used to validate the checks."""

from __future__ import annotations

import re


class ParseError(ValueError):
    def __init__(self, lineno: int, message: str):
        super().__init__(f"line {lineno}: {message}")
        self.lineno = lineno


class InterpolationError(ValueError):
    pass


SECTION_RE = re.compile(r"^\[(.*)\]$")
REF_RE = re.compile(r"\$\$|\$\{([^}]*)\}")


def parse(text: str) -> dict[str, dict[str, str]]:
    data: dict[str, dict[str, str]] = {}
    section = None
    last_key = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped[0] in "#;":
            last_key = None
            continue
        if raw[0].isspace() and last_key is not None:
            data[section][last_key] += "\n" + stripped
            continue
        m = SECTION_RE.match(stripped)
        if m:
            section = m.group(1).strip()
            data.setdefault(section, {})
            last_key = None
            continue
        idx = [i for i in (stripped.find("="), stripped.find(":")) if i >= 0]
        if not idx:
            raise ParseError(lineno, "expected a section or key")
        i = min(idx)
        key, value = stripped[:i].strip().lower(), stripped[i + 1:].strip()
        if not key:
            raise ParseError(lineno, "empty key")
        if section is None:
            raise ParseError(lineno, "key before any section")
        sect = data[section]
        sect[key] = value
        last_key = key

    def resolve(sec: str, key: str, stack: tuple) -> str:
        if (sec, key) in stack:
            raise InterpolationError(f"cycle at {sec}:{key}")
        if sec not in data or key not in data[sec]:
            raise InterpolationError(f"missing {sec}:{key}")

        def repl(m):
            if m.group(0) == "$$":
                return "$"
            ref = m.group(1)
            if ":" in ref:
                s, _, k = ref.partition(":")
            else:
                s, k = sec, ref
            return resolve(s, k.strip().lower(), stack + ((sec, key),))

        return REF_RE.sub(repl, data[sec][key])

    return {s: {k: resolve(s, k, ()) for k in keys}
            for s, keys in data.items()}
