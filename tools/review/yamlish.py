"""yamlish — a deliberately minimal, strict YAML subset for review documents.

Contract
--------
`loads(text)` parses exactly the supported subset below and returns Python
objects (mappings, lists, strings, booleans, integers, None).  `dumps(obj)`
is its canonical emitter; it emits only what `loads()` accepts and always
round-trips: `loads(dumps(x)) == x` for the supported shapes.  Everything
outside the subset is refused *by name* via `YamlishError` — error messages
are precise enough to fix the source document.

Supported syntax
----------------
- Blank lines and whole-line comments starting with ``#``; a trailing
  ``  # comment`` is allowed only when the ``#`` is preceded by whitespace.
- Block mappings: ``key: value``, or ``key:`` followed by a more-indented
  block.  Keys are plain ``[A-Za-z0-9_.-]+`` identifiers or single/quoted
  strings (single: ``''`` escape; double: ``\\"`` ``\\\\`` ``\\n`` ``\\t``).
- Block sequences: ``- value`` or ``- key: value`` with following keys
  aligned to the mapping indentation (the common ``  - id: X`` with
  ``    severity: y`` form).  Nested sequences are allowed.
- Scalars: plain (trimmed), quoted strings, ``true``/``false`` (lowercase
  only), ``null``/``~``, integers matching ``^-?[0-9]+$``.  Floats are NOT
  supported and are refused by name.  Everything else stays a string.
- Block scalars: ``key: |`` (literal; ``|-`` identical), ``key: >`` (folded:
  lines joined with single spaces, blank line means a newline).  A trailing
  newline is dropped from block scalars; strings whose last character is a
  newline are emitted with a double-quoted scalar instead so that they
  round-trip.
- Empty flow collections ``[]`` and ``{}`` (empty list / empty mapping).

Named refusals (each raised as YamlishError)
--------------------------------------------
- tabs used for indentation (any literal tab character)
- anchors and aliases (``&name``, ``*name``)
- tags (``!name``)
- document markers (``---``, ``...``); a second document started with
  ``---`` after content is refused as "multiple documents are not supported"
- non-empty flow collections (``[a, b]``, ``{a: 1}``)
- duplicate mapping keys (message names the key and the line)
- inconsistent indentation (message names the line)
- a sequence item with a mapping key on a following line at the wrong indent
  (message names the line)
- unterminated quoted scalar
- invalid escape in a double-quoted scalar
- floats (``1.5``, ``.5``, ``1e3`` — refused by name)
- additional strictness refusals, all named: multi-line plain scalars,
  mixing mapping keys and sequence items at one indentation, malformed
  keys, content after a quoted scalar, misplaced block-scalar headers,
  complex (``? ``) keys, backslashes outside double quotes.

CLI (``python3 -m tools.review.yamlish loads <file>``)
-------------
Prints ``json.dumps(result, indent=2, sort_keys=True)``.  Exit codes:
0 = ok, 1 = refusal/parse error (printed to stderr), 2 = usage error.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_INT_RE = re.compile(r"^-?[0-9]+$")
_FLOAT_RE = re.compile(r"^[+-]?(\.[0-9]+|[0-9]+(\.[0-9]*)?([eE][+-]?[0-9]+)?)$")
_PLAIN_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_INDICATOR_RE = re.compile(r"(^|\s)([&*!])")
_PLAIN_BAD_START = frozenset("#&*!|>%@`\"'\\[],{}?:- ")


class YamlishError(ValueError):
    """A named refusal from the yamlish parser/emitter.

    ``str(exc)`` is always ``"yamlish: <message> (line <N>)"``; the line
    suffix is omitted for errors not tied to a source line (e.g. bad
    arguments to ``dumps()``).
    """

    def __init__(self, message: str, line: int | None = None) -> None:
        self.message = message
        self.line = line
        super().__init__(message)

    def __str__(self) -> str:
        if self.line is None:
            return f"yamlish: {self.message}"
        return f"yamlish: {self.message} (line {self.line})"


class _Parser:
    """Line-oriented recursive-descent parser for the supported subset."""

    def __init__(self, text: str) -> None:
        # Each entry: (line number from 1, indentation in spaces, content
        # after comment stripping INCLUDING leading whitespace).  raw keeps
        # the untouched source lines for verbatim block-scalar content.
        self.raw: list[str] = []
        self.lines: list[tuple[int, int, str]] = []
        self._prepare(text)

    # -- line preparation -------------------------------------------------

    def _prepare(self, text: str) -> None:
        text = text.replace("\r\n", "\n")
        started = False
        for idx, raw in enumerate(text.split("\n")):
            self.raw.append(raw)
            lineno = idx + 1
            if "\t" in raw:
                raise YamlishError(
                    "tabs are not allowed in the document (indent with spaces)", lineno
                )
            cbody = self._strip_comment(raw, lineno)
            indent = len(cbody) - len(cbody.lstrip(" "))
            self.lines.append((lineno, indent, cbody))
            if not cbody.strip():
                continue
            # Marker detection uses the RAW line: a '...' or '---' that is
            # only a comment on its line is not a document marker (and may
            # be verbatim block-scalar content).
            stripped_raw = raw.strip()
            if stripped_raw in ("---", "..."):
                if stripped_raw == "---" and started:
                    raise YamlishError("multiple documents are not supported", lineno)
                raise YamlishError(
                    "document markers ('---' and '...') are not supported", lineno
                )
            started = True

    @staticmethod
    def _strip_comment(raw: str, lineno: int) -> str:
        """Return the line with a trailing comment removed.

        Walks the full line tracking quote state (unterminated quotes and
        single backslashes outside double quotes are refusals on their own).
        """
        i, n = 0, len(raw)
        state: str | None = None
        cut = n
        while i < n:
            c = raw[i]
            if state is None:
                # A quote only starts a quoted scalar at a token boundary; a
                # mid-word apostrophe in a plain scalar stays literal.
                if c in ("'", '"') and (i == 0 or raw[i - 1] == " "):
                    state = c
                elif c == "\\":
                    raise YamlishError(
                        "backslashes are only allowed inside double-quoted scalars",
                        lineno,
                    )
                elif c == "#" and (i == 0 or raw[i - 1] == " "):
                    cut = i
                    break
            elif state == "'":
                if c == "'":
                    if i + 1 < n and raw[i + 1] == "'":
                        i += 1  # escaped quote: skip the second one
                    else:
                        state = None
            else:  # state == '"'
                if c == "\\":
                    if i + 1 >= n:
                        raise YamlishError("unterminated quoted scalar", lineno)
                    i += 1  # skip the escaped character (validated later)
                elif c == '"':
                    state = None
            i += 1
        if state is not None:
            raise YamlishError("unterminated quoted scalar", lineno)
        return raw[:cut]

    # -- small scanning helpers ------------------------------------------

    @staticmethod
    def _is_dash(text: str) -> bool:
        return text == "-" or text.startswith("- ")

    @staticmethod
    def _colon(text: str) -> int | None:
        """Index of the first ':' outside quotes followed by a space or EOL."""
        state = None
        i, n = 0, len(text)
        while i < n:
            c = text[i]
            if state is None:
                if c in ("'", '"') and (i == 0 or text[i - 1] == " "):
                    state = c
                elif c == ":" and (i + 1 == n or text[i + 1] == " "):
                    return i
            elif state == "'":
                if c == "'":
                    if i + 1 < n and text[i + 1] == "'":
                        i += 1
                    else:
                        state = None
            else:  # double-quoted
                if c == "\\":
                    i += 1
                elif c == '"':
                    state = None
            i += 1
        return None

    def _next(self, i: int) -> int:
        """First index >= i whose line is not blank/comment-only."""
        n = len(self.lines)
        while i < n and not self.lines[i][2].strip():
            i += 1
        return i

    # -- entry points -----------------------------------------------------

    def parse(self) -> object:
        i = self._next(0)
        if i >= len(self.lines):
            return None
        value, i = self._value(i)
        j = self._next(i)
        if j < len(self.lines):
            if isinstance(value, (dict, list)):
                raise YamlishError("inconsistent indentation", self.lines[j][0])
            raise YamlishError("plain scalars may not span multiple lines", self.lines[j][0])
        return value

    def _value(self, i: int) -> tuple[object, int]:
        lineno, indent, cbody = self.lines[i]
        text = cbody[indent:]
        if self._is_dash(text):
            return self._sequence(i, indent)
        if self._colon(text) is not None:
            return self._mapping(i, indent)
        return self._scalar(text, lineno), i + 1

    # -- block structures ------------------------------------------------

    def _mapping(self, i: int, indent: int) -> tuple[dict, int]:
        d: dict = {}
        prev_plain = True
        while True:
            i = self._next(i)
            if i >= len(self.lines):
                return d, i
            lineno, ind, cbody = self.lines[i]
            if ind < indent:
                return d, i
            if ind > indent:
                if prev_plain:
                    raise YamlishError("plain scalars may not span multiple lines", lineno)
                raise YamlishError("inconsistent indentation", lineno)
            text = cbody[ind:]
            if self._is_dash(text):
                raise YamlishError(
                    "cannot mix mapping keys and sequence items at the same indentation",
                    lineno,
                )
            if text.startswith("? "):
                raise YamlishError("complex keys ('? ') are not supported", lineno)
            c = self._colon(text)
            if c is None:
                if prev_plain:
                    raise YamlishError("plain scalars may not span multiple lines", lineno)
                raise YamlishError("expected a 'key:' mapping entry", lineno)
            key = self._key(text[:c], lineno)
            if key in d:
                raise YamlishError(f"duplicate key {key!r}", lineno)
            rest = text[c + 1 :]
            if rest and rest[0] == " ":
                rest = rest[1:]
            value, i, plain = self._entry(rest, lineno, i, indent)
            d[key] = value
            prev_plain = plain

    def _sequence(self, i: int, seq_indent: int) -> tuple[list, int]:
        out: list = []
        prev_plain = True
        while True:
            i = self._next(i)
            if i >= len(self.lines):
                return out, i
            lineno, ind, cbody = self.lines[i]
            if ind < seq_indent:
                return out, i
            if ind > seq_indent:
                if prev_plain:
                    raise YamlishError("plain scalars may not span multiple lines", lineno)
                raise YamlishError("inconsistent indentation", lineno)
            text = cbody[ind:]
            if not self._is_dash(text):
                if self._colon(text) is not None:
                    raise YamlishError(
                        "sequence item with a mapping key on a following line at the "
                        "wrong indent",
                        lineno,
                    )
                raise YamlishError("expected a '- ' sequence item", lineno)
            rest = text[2:] if text.startswith("- ") else ""
            item, i, plain = self._item(rest, lineno, i, seq_indent)
            out.append(item)
            prev_plain = plain

    def _item(
        self, rest: str, lineno: int, i: int, dash_indent: int
    ) -> tuple[object, int, bool]:
        """Parse one sequence item whose dash sits on self.lines[i]."""
        if rest.strip() == "":
            # bare '-' : nested value on following lines, else null
            j = self._next(i + 1)
            if j < len(self.lines) and self.lines[j][1] > dash_indent:
                value, j = self._value(j)
            else:
                value, j = None, j
            return value, j, False
        if self._is_dash(rest) and rest.strip() != "-":
            # compact nested sequence: '- - a' — the inner dash lives on the
            # current line at virtual column dash_indent + 2, so rewrite the
            # line before recursing (the parser consumes its lines read-only
            # otherwise and we never revisit this line).
            self.lines[i] = (self.lines[i][0], dash_indent + 2, self.lines[i][2])
            value, j = self._sequence(i, dash_indent + 2)
            return value, j, False
        r = rest.strip()
        if r in ("|", "|-", ">"):
            value, j = self._block(i + 1, dash_indent, folded=(r == ">"))
            return value, j, False
        if r[0] in "|>":
            raise YamlishError(
                "block scalar headers must be exactly '|', '|-', or '>'", lineno
            )
        c = self._colon(rest)
        if c is None:
            return self._scalar(rest, lineno), i + 1, True
        # inline mapping item: '- key: value'
        key = self._key(rest[:c], lineno)
        vtext = rest[c + 1 :]
        if vtext and vtext[0] == " ":
            vtext = vtext[1:]
        value, j, plain = self._entry(vtext, lineno, i, dash_indent + 2)
        item: dict = {key: value}
        key_col = dash_indent + 2
        while True:
            k = self._next(j)
            if k >= len(self.lines) or self.lines[k][1] < key_col:
                return item, k, plain
            ln2, ind2, cb2 = self.lines[k]
            if ind2 > key_col:
                if plain:
                    raise YamlishError("plain scalars may not span multiple lines", ln2)
                raise YamlishError("inconsistent indentation", ln2)
            t2 = cb2[ind2:]
            c2 = self._colon(t2)
            if self._is_dash(t2) or c2 is None:
                raise YamlishError("inconsistent indentation", ln2)
            kk = self._key(t2[:c2], ln2)
            if kk in item:
                raise YamlishError(f"duplicate key {kk!r}", ln2)
            v2 = t2[c2 + 1 :]
            if v2 and v2[0] == " ":
                v2 = v2[1:]
            vval, j, plain = self._entry(v2, ln2, k, key_col)
            item[kk] = vval

    def _entry(
        self, rest: str, lineno: int, i: int, parent_indent: int
    ) -> tuple[object, int, bool]:
        """Parse the value following 'key:' on self.lines[i].

        Returns (value, next_raw_index, was_plain_scalar).
        """
        rest = rest.strip()
        if rest == "":
            j = self._next(i + 1)
            if j < len(self.lines) and self.lines[j][1] > parent_indent:
                value, j = self._value(j)
                return value, j, False
            return None, j, False
        if rest in ("|", "|-", ">"):
            value, j = self._block(i + 1, parent_indent, folded=(rest == ">"))
            return value, j, False
        if rest[0] in "|>":
            raise YamlishError(
                "block scalar headers must be exactly '|', '|-', or '>'", lineno
            )
        return self._scalar(rest, lineno), i + 1, True

    def _block(self, i: int, parent_indent: int, folded: bool) -> tuple[str, int]:
        """Collect block-scalar content: blank lines, or lines indented
        strictly deeper than parent_indent, dedented to the shallowest
        content line.  Content is taken from the RAW source lines (verbatim:
        '#', quotes, and backslashes inside a block scalar are content)."""
        collected: list[tuple[str, bool]] = []
        while i < len(self.raw):
            raw = self.raw[i]
            if not raw.strip():
                collected.append(("", True))
            else:
                ind = len(raw) - len(raw.lstrip(" "))
                if ind > parent_indent:
                    collected.append((raw, False))
                else:
                    break
            i += 1
        dd = min((len(cb) - len(cb.lstrip(" ")) for cb, blank in collected if not blank), default=0)
        contents = [cb[dd:] if not blank else "" for cb, blank in collected]
        if folded:
            out = ""
            for line in contents:
                if line == "":
                    out += "\n"
                else:
                    if out and not out.endswith("\n"):
                        out += " "
                    out += line
            if out.endswith("\n"):
                out = out[:-1]
            return out, i
        out = "\n".join(contents)
        if out.endswith("\n"):
            out = out[:-1]
        return out, i

    # -- scalars and keys ------------------------------------------------

    def _scalar(self, text: str, lineno: int) -> object:
        s = text.strip()
        if s == "":
            return None
        if s in ("[]", "{}"):
            return [] if s == "[]" else {}
        if s[0] in "[{":
            raise YamlishError(
                "only empty flow collections '[]' and '{}' are supported", lineno
            )
        if s[0] in "'\"":
            return self._quoted(s, lineno)
        if s[0] in "|>":
            raise YamlishError(
                "block scalar headers ('|', '>') are only allowed directly after "
                "'key:' or '- '",
                lineno,
            )
        if s[0] in "&*":
            raise YamlishError("anchors and aliases ('&'/'*') are not supported", lineno)
        if s[0] == "!":
            raise YamlishError("tags ('!name') are not supported", lineno)
        if s.startswith("- ") or s.startswith("? "):
            raise YamlishError("plain scalars may not begin with '- ' or '? '", lineno)
        m = _INDICATOR_RE.search(s)
        if m:
            if m.group(2) == "!":
                raise YamlishError("tags ('!name') are not supported", lineno)
            raise YamlishError("anchors and aliases ('&'/'*') are not supported", lineno)
        if s == "true":
            return True
        if s == "false":
            return False
        if s in ("null", "~"):
            return None
        if _INT_RE.fullmatch(s):
            return int(s)
        if _FLOAT_RE.fullmatch(s):
            raise YamlishError("floats are not supported", lineno)
        return s

    def _quoted(self, s: str, lineno: int) -> str:
        q = s[0]
        i, n = 1, len(s)
        out: list[str] = []
        while i < n:
            c = s[i]
            if q == "'":
                if c == "'":
                    if i + 1 < n and s[i + 1] == "'":
                        out.append("'")
                        i += 2
                        continue
                    i += 1
                    break
            else:
                if c == '"':
                    i += 1
                    break
                if c == "\\":
                    if i + 1 >= n:
                        raise YamlishError("unterminated quoted scalar", lineno)
                    nxt = s[i + 1]
                    if nxt == "n":
                        out.append("\n")
                    elif nxt == "t":
                        out.append("\t")
                    elif nxt == '"':
                        out.append('"')
                    elif nxt == "\\":
                        out.append("\\")
                    else:
                        raise YamlishError(
                            f"invalid escape '\\{nxt}' in a double-quoted scalar", lineno
                        )
                    i += 2
                    continue
            out.append(c)
            i += 1
        else:
            raise YamlishError("unterminated quoted scalar", lineno)
        if s[i:].strip():
            raise YamlishError("content after a quoted scalar", lineno)
        return "".join(out)

    def _key(self, keytext: str, lineno: int) -> str:
        s = keytext
        if s.startswith(("'", '"')):
            return self._quoted(s, lineno)
        if s != s.strip() or not _PLAIN_KEY_RE.fullmatch(s):
            raise YamlishError(f"invalid mapping key {s!r}", lineno)
        return s


def loads(text: str) -> object:
    """Parse a yamlish document. Refuses everything outside the subset by name."""
    if not isinstance(text, str):
        raise YamlishError("loads() expects a string document")
    return _Parser(text).parse()


# -- emitter --------------------------------------------------------------

def _plain_safe(s: str) -> bool:
    """Can `s` be emitted as a plain scalar and load back as itself?"""
    if not s or s.strip() != s:
        return False
    if s[0] in _PLAIN_BAD_START:
        return False
    if " #" in s:
        return False
    # These would either parse as a mapping item (': '), be refused by the
    # parser (backslash outside quotes, document markers, indicator
    # characters), or change type.
    if ": " in s or s.endswith(":"):
        return False
    if "\\" in s:
        return False
    if s in ("---", "..."):
        return False
    if _INDICATOR_RE.search(s):
        return False
    # A quote preceded by whitespace would open a quoted-scalar state in the
    # parser's line scanner (token-start rule) and never close.
    if re.search(r"(^|\s)['\"]", s):
        return False
    if s in ("true", "false", "null", "~"):
        return False
    if _INT_RE.fullmatch(s) or _FLOAT_RE.fullmatch(s):
        return False
    if any(ord(ch) < 32 for ch in s):
        return False
    return True


def _single(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _double(s: str) -> str:
    return (
        '"'
        + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
        + '"'
    )


def _block_line_ok(line: str) -> bool:
    """A line is safe inside an emitted literal block if the parser's line
    scanner will not trip over it (backslash refusal, unterminated quote,
    document marker)."""
    if "\\" in line:
        return False
    state = None
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if state is None:
            if c in ("'", '"') and (i == 0 or line[i - 1] == " "):
                state = c
        elif state == "'":
            if c == "'":
                if i + 1 < n and line[i + 1] == "'":
                    i += 1
                else:
                    state = None
        else:  # double-quoted
            if c == "\\":
                i += 1
            elif c == '"':
                state = None
        i += 1
    return state is None


def _is_block_str(s: str) -> bool:
    """Literal-block eligible: multi-line, no trailing newline, no tabs or
    carriage returns, and no lines that would not round-trip verbatim
    (whitespace-only lines, document markers, or lines the parser's scanner
    would refuse).  Every non-blank line must include at least one line
    without leading whitespace, otherwise the block's auto-dedent would eat
    one space from each line."""
    if "\n" not in s or s.endswith("\n") or "\t" in s or "\r" in s:
        return False
    lines = s.split("\n")
    if any(line != "" and line.strip() == "" for line in lines):
        return False
    if any(line.strip() in ("---", "...") for line in lines):
        return False
    nonblank = [line for line in lines if line != ""]
    if nonblank and all(line.startswith(" ") for line in nonblank):
        return False
    return all(_block_line_ok(line) for line in lines)


def _scalar_form(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        raise YamlishError("floats are not supported")
    if isinstance(value, dict):
        return "{}"  # only empty mappings can be emitted as flow
    if isinstance(value, list):
        return "[]"  # only empty sequences can be emitted as flow
    if isinstance(value, str):
        if value == "":
            return "''"
        if "\n" not in value and "\t" not in value:
            if _plain_safe(value):
                return value
            return _single(value)
        return _double(value)
    raise YamlishError(f"cannot emit values of type {type(value).__name__}")


def _key_form(k: object) -> str:
    if not isinstance(k, str):
        raise YamlishError("mapping keys must be strings")
    if "\n" in k or "\r" in k:
        raise YamlishError("mapping keys may not contain newlines")
    if _PLAIN_KEY_RE.fullmatch(k):
        return k
    if "\t" in k:
        return _double(k)
    return _single(k)


def _rest_items(d: dict) -> dict:
    it = iter(d.items())
    next(it, None)
    return dict(it)


def _emit_map_entries(m: dict, col: int, indent: int, lines: list[str]) -> None:
    for k, v in m.items():
        prefix = " " * col + _key_form(k) + ":"
        if isinstance(v, str) and _is_block_str(v):
            lines.append(prefix + " |")
            lines.extend(" " * (col + indent) + ln for ln in v.split("\n"))
        elif isinstance(v, (dict, list)) and v:
            lines.append(prefix)
            lines.extend(_emit_value(v, col + indent, indent))
        else:
            lines.append(prefix + " " + _scalar_form(v))


def _emit_seq_items(seq: list, col: int, indent: int, lines: list[str]) -> None:
    pad = " " * col
    for item in seq:
        if isinstance(item, dict) and item:
            first = next(iter(item.items()))
            k0, v0 = first[0], first[1]
            pre = pad + "- " + _key_form(k0)
            if isinstance(v0, str) and _is_block_str(v0):
                lines.append(pre + ": |")
                lines.extend(
                    " " * (col + 2 + indent) + ln for ln in v0.split("\n")
                )
            elif isinstance(v0, (dict, list)) and v0:
                lines.append(pre + ":")
                lines.extend(_emit_value(v0, col + 2 + indent, indent))
            else:
                lines.append(pre + ": " + _scalar_form(v0))
            rest = _rest_items(item)
            if rest:
                _emit_map_entries(rest, col + 2, indent, lines)
        elif isinstance(item, list) and item:
            inner = _emit_value(item, col + 2, indent)
            lines.append(pad + "- " + inner[0].lstrip())
            lines.extend(inner[1:])
        elif isinstance(item, str) and _is_block_str(item):
            lines.append(pad + "- |")
            lines.extend(" " * (col + indent) + ln for ln in item.split("\n"))
        else:
            lines.append(pad + "- " + _scalar_form(item))


def _emit_value(value: object, col: int, indent: int) -> list[str]:
    if isinstance(value, dict):
        if not value:
            return [" " * col + "{}"]
        lines: list[str] = []
        _emit_map_entries(value, col, indent, lines)
        return lines
    if isinstance(value, list):
        if not value:
            return [" " * col + "[]"]
        lines = []
        _emit_seq_items(value, col, indent, lines)
        return lines
    return [" " * col + _scalar_form(value)]


def dumps(obj: object, indent: int = 2) -> str:
    """Canonical emitter. Dict insertion order is preserved (never sorted);
    multi-line strings under mapping keys are emitted as literal blocks."""
    if not isinstance(indent, int) or isinstance(indent, bool) or indent < 1:
        raise YamlishError("indent must be a positive integer")
    return "\n".join(_emit_value(obj, 0, indent)) + "\n"


_USAGE = "usage: yamlish loads <file>"


def main(argv: list[str]) -> int:
    """`loads <file>`: print JSON rendering; 0 ok, 1 parse refusal, 2 usage."""
    if len(argv) != 3 or argv[1] != "loads":
        print(_USAGE, file=sys.stderr)
        return 2
    path = argv[2]
    try:
        data = loads(Path(path).read_text(encoding="utf-8"))
    except YamlishError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError:
        print(f"yamlish: cannot read {path}", file=sys.stderr)
        return 1
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))