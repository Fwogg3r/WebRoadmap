"""Frontmatter for FORMAT.md: a small YAML subset with lossless round-trip.

Only scalars, quoted strings, inline lists ``[a, b]`` and block lists (``- a``)
are understood. A key whose value is not changed is written back byte for byte
(comments, quoting style and unknown keys included); a changed or new key is
written in the canonical style of FORMAT.md.
"""

from __future__ import annotations

import copy
import json
import re

KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:(.*)$")
INT_RE = re.compile(r"^[-+]?\d+$")
FLOAT_RE = re.compile(r"^[-+]?(\d+\.\d*|\.\d+)([eE][-+]?\d+)?$")


class FormatError(ValueError):
    pass


def _strip_comment(s: str) -> str:
    """Cut an unquoted ` # comment` (YAML needs whitespace before '#')."""
    m = re.search(r"(^|\s)#", s)
    return s[: m.start()].rstrip() if m else s.rstrip()


def _read_quoted(s: str) -> tuple[str, str]:
    """Parse a quoted string at the start of s; return (value, rest)."""
    q = s[0]
    if q == "'":
        out, i = [], 1
        while i < len(s):
            if s[i] == "'":
                if i + 1 < len(s) and s[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                return "".join(out), s[i + 1 :]
            out.append(s[i])
            i += 1
        raise FormatError(f"unterminated string: {s}")
    i = 1
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == '"':
            try:
                return json.loads(s[: i + 1]), s[i + 1 :]
            except json.JSONDecodeError as e:
                raise FormatError(f"bad string {s[: i + 1]}: {e}") from None
        i += 1
    raise FormatError(f"unterminated string: {s}")


def parse_scalar(s: str):
    s = s.strip()
    if not s:
        return None
    if s[0] in "\"'":
        v, rest = _read_quoted(s)
        if _strip_comment(rest).strip():
            raise FormatError(f"junk after string: {s}")
        return v
    s = _strip_comment(s)
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~", ""):
        return None
    if INT_RE.match(s):
        return int(s)
    if FLOAT_RE.match(s):
        return float(s)
    return s


def _split_inline_list(s: str) -> list:
    inner = s.strip()
    assert inner[0] == "["
    inner = inner[1:]
    items, cur = [], ""
    i = 0
    while i < len(inner):
        c = inner[i]
        if c in "\"'":
            v, rest = _read_quoted(inner[i:])
            cur += inner[i : len(inner) - len(rest)]
            i = len(inner) - len(rest)
            continue
        if c == ",":
            items.append(cur)
            cur = ""
        elif c == "]":
            if _strip_comment(inner[i + 1 :]).strip():
                raise FormatError(f"junk after list: {s}")
            if cur.strip() or items:
                items.append(cur)
            return [parse_scalar(x) for x in items if x.strip() != ""]
        else:
            cur += c
        i += 1
    raise FormatError(f"unterminated list: {s}")


def parse_value(rest: str, block: list[str]):
    r = rest.strip()
    if r.startswith("["):
        return _split_inline_list(r)
    if r and not r.startswith("#"):
        if block:
            raise FormatError(f"unexpected indented lines after '{rest.strip()}'")
        return parse_scalar(r)
    items = [ln.strip() for ln in block if ln.strip() and not ln.strip().startswith("#")]
    if not items:
        return None
    out = []
    for ln in items:
        if not ln.startswith("-"):
            raise FormatError(f"nested mappings are not supported: {ln}")
        out.append(parse_scalar(ln[1:]))
    return out


def _plain_ok(s: str) -> bool:
    if s != s.strip() or not s or "\n" in s:
        return False
    if s[0] in "[]{}#&*!|>%@`\"',?:-" and not re.match(r"^-?\d", s):
        return False
    if ": " in s or " #" in s or s.endswith(":"):
        return False
    return parse_scalar(s) == s


def emit_scalar(v, in_list: bool = False) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if _plain_ok(s) and not (in_list and ("," in s or "]" in s or "[" in s)):
        return s
    return json.dumps(s, ensure_ascii=False)


def emit_value(v) -> str:
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(emit_scalar(x, True) for x in v) + "]"
    return emit_scalar(v)


class Doc:
    """A Markdown file with frontmatter. ``fields`` is the editable view."""

    def __init__(self, fields=None, body: str = ""):
        self.fields: dict = dict(fields or {})
        self.body = body
        self._raw: dict[str, list[str]] = {}
        self._orig: dict = {}
        self._tail: list[str] = []

    @classmethod
    def parse(cls, text: str) -> "Doc":
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if text.startswith("﻿"):
            text = text[1:]
        lines = text.split("\n")
        if not lines or lines[0].strip() != "---":
            return cls({}, text)
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is None:
            raise FormatError("frontmatter not closed with '---'")
        fm = lines[1:end]
        body = "\n".join(lines[end + 1 :])
        doc = cls({}, body)
        pending: list[str] = []
        key = None
        chunks: list[tuple[str, list[str], str, list[str]]] = []
        for ln in fm:
            m = KEY_RE.match(ln)
            if m and not ln[0].isspace():
                key = m.group(1)
                chunks.append((key, pending + [ln], m.group(2), []))
                pending = []
            elif key is not None and ln.strip() and (ln[0].isspace() or ln.lstrip().startswith("-")):
                chunks[-1][1].append(ln)
                chunks[-1][3].append(ln)
            elif not ln.strip() or ln.lstrip().startswith("#"):
                pending.append(ln)
            else:
                raise FormatError(f"cannot parse frontmatter line: {ln!r}")
        doc._tail = pending
        for k, raw, rest, block in chunks:
            if k in doc.fields:
                raise FormatError(f"duplicate key: {k}")
            doc.fields[k] = parse_value(rest, block)
            doc._raw[k] = raw
        doc._orig = copy.deepcopy(doc.fields)
        return doc

    def get(self, key, default=None):
        v = self.fields.get(key)
        return default if v is None else v

    def set(self, key, value):
        """Set a field; None removes it."""
        if value is None:
            self.fields.pop(key, None)
        else:
            self.fields[key] = value

    def dump(self) -> str:
        out = ["---"]
        for k, v in self.fields.items():
            if k in self._raw and k in self._orig and self._orig[k] == v and type(self._orig[k]) is type(v):
                out.extend(self._raw[k])
            else:
                out.append(f"{k}: {emit_value(v)}")
        out.extend(self._tail)
        out.append("---")
        body = self.body.strip("\n")
        return "\n".join(out) + "\n" + (body + "\n" if body else "")
