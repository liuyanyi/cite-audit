"""Bounded, non-executing BibTeX reader and lossless selective exporter.

This is a source editor, not a BibTeX pretty-printer. Offsets are Python string
offsets into ``BibDocument.text``; UTF-8/surrogateescape makes round trips exact.
The service must verify proposal evidence and version identity before calling
``preview_export``. This module additionally rejects structurally unsafe edits.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

MAX_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 10_000
MAX_FIELDS = 256
MAX_DEPTH = 128
MAX_VALUE = 256 * 1024
MAX_EXPANSION = 32 * 1024 * 1024
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_:.+/-]*")
_BARE_VALUE = re.compile(r"[^\s,{}()#%\"=]+")
_HEADER = re.compile(r"(?m)^[ \t]*(@[A-Za-z][A-Za-z0-9_:.+/-]*\s*[({])")
_MONTHS = dict(
    zip(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
    )
)

# Whole-version replacement never inherits these from a previous publication.
VERSION_FIELDS = frozenset(
    [
        "author",
        "editor",
        "title",
        "subtitle",
        "titleaddon",
        "shorttitle",
        "booktitle",
        "booksubtitle",
        "booktitleaddon",
        "maintitle",
        "mainsubtitle",
        "maintitleaddon",
        "journal",
        "journaltitle",
        "journalsubtitle",
        "shortjournal",
        "year",
        "month",
        "day",
        "date",
        "origdate",
        "eventdate",
        "urldate",
        "volume",
        "number",
        "issue",
        "part",
        "pages",
        "pagetotal",
        "eid",
        "articleno",
        "article-number",
        "doi",
        "url",
        "eprint",
        "eprinttype",
        "eprintclass",
        "archiveprefix",
        "primaryclass",
        "arxivid",
        "isbn",
        "issn",
        "isrn",
        "publisher",
        "institution",
        "organization",
        "school",
        "address",
        "location",
        "edition",
        "series",
        "chapter",
        "type",
        "howpublished",
        "note",
        "annote",
        "abstract",
        "keywords",
        "language",
        "langid",
        "eventtitle",
        "eventtitleaddon",
        "venue",
        "eventyear",
        "crossref",
        "xref",
        "xdata",
        "related",
        "relatedtype",
        "relatedstring",
        "pubstate",
        "version",
        "reportnumber",
    ]
)
_ALLOWED_COMMANDS = frozenset(
    [
        "TeX",
        "LaTeX",
        "BibTeX",
        "textit",
        "textbf",
        "texttt",
        "textsc",
        "textrm",
        "textsf",
        "textsl",
        "textnormal",
        "emph",
        "enquote",
        "ensuremath",
        "mathrm",
        "mathbf",
        "mathit",
        "mathsf",
        "mathbb",
        "mathcal",
        "operatorname",
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "varepsilon",
        "zeta",
        "eta",
        "theta",
        "vartheta",
        "iota",
        "kappa",
        "lambda",
        "mu",
        "nu",
        "xi",
        "omicron",
        "pi",
        "varpi",
        "rho",
        "varrho",
        "sigma",
        "varsigma",
        "tau",
        "upsilon",
        "phi",
        "varphi",
        "chi",
        "psi",
        "omega",
        "Gamma",
        "Delta",
        "Theta",
        "Lambda",
        "Xi",
        "Pi",
        "Sigma",
        "Upsilon",
        "Phi",
        "Psi",
        "Omega",
        "times",
        "cdot",
        "pm",
        "mp",
        "leq",
        "geq",
        "neq",
        "approx",
        "infty",
        "partial",
        "nabla",
        "frac",
        "sqrt",
        "ae",
        "AE",
        "oe",
        "OE",
        "aa",
        "AA",
        "o",
        "O",
        "l",
        "L",
        "ss",
        "i",
        "j",
        "u",
        "v",
        "H",
        "c",
        "k",
        "b",
        "d",
        "r",
        "t",
        "textbackslash",
        "textasciitilde",
        "textasciicircum",
        "textendash",
        "textemdash",
        "textquotedblleft",
        "textquotedblright",
        "textquoteleft",
        "textquoteright",
        "url",
    ]
)


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: str = "error"
    line: int = 1
    column: int = 1
    entry_id: str | None = None


@dataclass(frozen=True)
class FieldSpan:
    name: str
    name_start: int
    name_end: int
    equals: int
    value_start: int
    value_end: int
    comma: int | None
    expression: str
    uses_macro: bool = False


@dataclass
class BibEntry:
    entry_id: str
    index: int
    key: str
    entry_type: str
    fields: dict[str, str]
    raw: str
    start: int
    end: int
    diagnostics: list[Diagnostic] = field(default_factory=list)
    editable: bool = True
    field_spans: dict[str, FieldSpan] = field(default_factory=dict, repr=False)
    inherited_fields: set[str] = field(default_factory=set)
    type_start: int = 0
    type_end: int = 0
    close_start: int = 0
    crossref_dependents: set[str] = field(default_factory=set, repr=False)


@dataclass
class BibDocument:
    original: bytes
    text: str
    entries: list[BibEntry]
    diagnostics: list[Diagnostic]
    line_ending: str = "\n"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.original).hexdigest()


@dataclass(frozen=True)
class ExportSelection:
    entry_id: str
    proposal_type: str
    fields: Mapping[str, str | None]
    entry_type: str | None = None
    version_id: str | None = None


@dataclass(frozen=True)
class ExportPreview:
    content: bytes
    changes: list[dict]
    blocked: list[dict]
    sha256: str


class BibLimitError(ValueError):
    """Input exceeds a documented parsing resource limit."""


class _SyntaxError(ValueError):
    def __init__(self, message: str, position: int, code: str = "malformed_entry"):
        super().__init__(message)
        self.position, self.code = position, code


def _diag(
    text: str, code: str, message: str, pos: int, entry_id: str | None = None, severity: str = "error"
) -> Diagnostic:
    line = text.count("\n", 0, pos) + 1
    return Diagnostic(code, message, severity, line, pos - text.rfind("\n", 0, pos), entry_id)


def _safe_value(value: str) -> str:
    """Validate a braced literal; never run or expand any TeX command."""
    if not isinstance(value, str) or len(value) > MAX_VALUE:
        raise ValueError("Field value must be text within the 256 KiB limit.")
    if any((ord(c) < 32 and c not in "\t\r\n") or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("Field value contains control characters or invalid Unicode.")
    if "^^" in value:
        raise ValueError("TeX character-code escapes are not allowed in replacement values.")
    depth, i = 0, 0
    while i < len(value):
        c = value[i]
        if c == "\\":
            if i + 1 == len(value):
                raise ValueError("A trailing backslash would escape the field boundary.")
            command = re.match(r"[A-Za-z]+", value[i + 1 :])
            if command:
                if command[0] not in _ALLOWED_COMMANDS:
                    raise ValueError(f"Unsupported or unsafe TeX command: \\{command[0]}.")
                i += len(command[0]) + 1
                continue
            if value[i + 1] not in "{}%&#_$~^'`\"= .!,:;-/\\":
                raise ValueError("Unsupported TeX escape in replacement value.")
            i += 2
            continue
        if c == "%":
            raise ValueError("Literal percent must be escaped as \\% in a replacement value.")
        if c == "{":
            depth += 1
            if depth > MAX_DEPTH:
                raise ValueError("Field value nesting exceeds the safe limit.")
        elif c == "}":
            depth -= 1
            if depth < 0:
                raise ValueError("Unbalanced braces in replacement value.")
        i += 1
    if depth:
        raise ValueError("Unbalanced braces in replacement value.")
    return "{" + value + "}"


class _Parser:
    def __init__(self, raw: bytes):
        self.text = raw.decode("utf-8", "surrogateescape")
        self.document = BibDocument(raw, self.text, [], [], "\r\n" if "\r\n" in self.text else "\n")
        self.macros = dict(_MONTHS)
        self.macro_names: set[str] = set()
        self.expanded = 0
        self.scan_work = 0

    def skip(self, pos: int) -> int:
        text = self.text
        while pos < len(text):
            if text[pos].isspace() or text[pos] == "\ufeff":
                pos += 1
            elif text[pos] == "%":
                end = text.find("\n", pos)
                pos = len(text) if end < 0 else end + 1
            else:
                break
        return pos

    def literal(self, pos: int) -> tuple[str, int]:
        text, opener = self.text, self.text[pos]
        start, depth, i = pos + 1, 1 if opener == "{" else 0, pos + 1
        while i < len(text):
            self.scan_work += 1
            if self.scan_work > max(1_000_000, len(text) * 8):
                raise BibLimitError("Malformed input exceeds the bounded parser work budget.")
            if i - start > MAX_VALUE:
                raise _SyntaxError("Field exceeds the 256 KiB limit.", start, "value_limit")
            c = text[i]
            if c == "\\":
                i += 2
                continue
            if c == "{":
                depth += 1
                if depth > MAX_DEPTH:
                    raise _SyntaxError("Value nesting exceeds 128 levels.", i, "nesting_limit")
            elif c == "}":
                depth -= 1
                if opener == "{" and depth == 0:
                    return text[start:i], i + 1
                if depth < 0:
                    raise _SyntaxError("Unbalanced brace inside quoted value.", i)
            elif c == '"' and opener == '"' and depth == 0:
                return text[start:i], i + 1
            i += 1
        raise _SyntaxError("Unterminated field value.", pos)

    def expression(self, pos: int) -> tuple[str, int, bool, list[str]]:
        parts, unresolved, uses_macro = [], [], False
        total, end = 0, pos
        while True:
            pos = self.skip(pos)
            if pos >= len(self.text):
                raise _SyntaxError("Expected a field value.", pos)
            if self.text[pos] in '{"':
                part, end = self.literal(pos)
            else:
                match = _BARE_VALUE.match(self.text, pos)
                if not match:
                    raise _SyntaxError("Expected a braced, quoted, numeric or macro value.", pos)
                token = match[0]
                end = pos + len(token)
                if token.isdecimal():
                    part = token
                elif _NAME.fullmatch(token):
                    uses_macro = True
                    part = self.macros.get(token.lower(), token)
                    if token.lower() not in self.macros:
                        unresolved.append(token)
                else:
                    raise _SyntaxError("Invalid unquoted value; use braces for literal text.", pos)
            total += len(part)
            if total > MAX_VALUE:
                raise _SyntaxError("Expanded value exceeds 256 KiB.", pos, "expansion_limit")
            parts.append(part)
            following = self.skip(end)
            if following < len(self.text) and self.text[following] == "#":
                pos = following + 1
                continue
            self.expanded += total
            if self.expanded > MAX_EXPANSION:
                raise BibLimitError("Total macro expansion exceeds 32 MiB.")
            return "".join(parts), end, uses_macro, unresolved

    def recover(self, start: int) -> int:
        match = _HEADER.search(self.text, start + 1)
        return match.start(1) if match else len(self.text)

    def parse(self) -> BibDocument:
        text, pos = self.text, 0
        bad = next((i for i, c in enumerate(text) if 0xDC80 <= ord(c) <= 0xDCFF), None)
        if bad is not None:
            self.document.diagnostics.append(
                _diag(
                    text,
                    "invalid_utf8",
                    "Invalid UTF-8 bytes retained unchanged; affected entries cannot be edited.",
                    bad,
                )
            )
        while pos < len(text):
            pos = self.skip(pos)
            if pos >= len(text):
                break
            if text[pos] != "@":
                # BibTeX treats text outside directives as comments.
                pos += 1
                continue
            if len(self.document.entries) >= MAX_ENTRIES:
                raise BibLimitError("Bib file exceeds the 10,000 entry limit.")
            start = pos
            name = _NAME.match(text, pos + 1)
            kind = name[0].lower() if name else "unknown"
            pos = self.skip(name.end() if name else pos + 1)
            if kind == "comment" and (pos >= len(text) or text[pos] not in "{("):
                next_at = text.find("@", pos)
                pos = len(text) if next_at < 0 else next_at
                continue
            if kind in {"comment", "string", "preamble"}:
                try:
                    pos = self.directive(kind, pos)
                except _SyntaxError as exc:
                    self.document.diagnostics.append(_diag(text, exc.code, str(exc), exc.position))
                    pos = self.recover(start)
                continue
            entry_id = f"entry-{len(self.document.entries) + 1}"
            entry = BibEntry(
                entry_id,
                len(self.document.entries),
                "",
                kind,
                {},
                "",
                start,
                start,
                type_start=start + 1,
                type_end=name.end() if name else start + 1,
            )
            self.document.entries.append(entry)
            try:
                if not name or pos >= len(text) or text[pos] not in "{(":
                    raise _SyntaxError("Expected @type{citation_key, ...}.", pos)
                close = "}" if text[pos] == "{" else ")"
                pos = self.skip(pos + 1)
                key_start = pos
                while pos < len(text) and text[pos] not in ",}\n\r()":
                    pos += 1
                entry.key = text[key_start:pos].strip()
                if not _valid_key(entry.key):
                    raise _SyntaxError("Missing or invalid citation key.", key_start, "invalid_key")
                if pos < len(text) and text[pos] == close:
                    entry.close_start, pos = pos, pos + 1
                else:
                    if pos >= len(text) or text[pos] != ",":
                        raise _SyntaxError("Expected comma after citation key.", pos)
                    pos = self.fields(entry, pos + 1, close)
                entry.end = pos
            except _SyntaxError as exc:
                entry.editable = False
                entry.diagnostics.append(_diag(text, exc.code, str(exc), exc.position, entry_id))
                pos = self.recover(start)
                entry.end = pos
            entry.raw = text[entry.start : entry.end]
            if any(0xDC80 <= ord(c) <= 0xDCFF for c in entry.raw):
                entry.editable = False
                entry.diagnostics.append(
                    _diag(text, "invalid_utf8", "Entry contains invalid UTF-8 bytes.", start, entry_id)
                )
        self.resolve_crossrefs()
        self.document.diagnostics.extend(d for e in self.document.entries for d in e.diagnostics)
        return self.document

    def directive(self, kind: str, pos: int) -> int:
        text = self.text
        if pos >= len(text) or text[pos] not in "{(":
            raise _SyntaxError(f"Expected delimiter after @{kind}.", pos, "malformed_directive")
        opener, close = text[pos], "}" if text[pos] == "{" else ")"
        if kind == "comment":
            if opener == "{":
                return self.literal(pos)[1]
            # Parenthesized comments may contain nested parentheses.
            depth, i = 1, pos + 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                depth += (text[i] == "(") - (text[i] == ")")
                if depth == 0:
                    return i + 1
                if depth > MAX_DEPTH:
                    raise _SyntaxError("Comment nesting limit exceeded.", i, "nesting_limit")
                i += 1
            raise _SyntaxError("Unterminated comment.", pos, "malformed_directive")
        pos = self.skip(pos + 1)
        name = None
        if kind == "string":
            name = _NAME.match(text, pos)
            if not name:
                raise _SyntaxError("Missing string macro name.", pos, "malformed_directive")
            pos = self.skip(name.end())
            if pos >= len(text) or text[pos] != "=":
                raise _SyntaxError("Expected = after macro name.", pos, "malformed_directive")
            pos = self.skip(pos + 1)
        value, end, _, unresolved = self.expression(pos)
        end = self.skip(end)
        if end < len(text) and text[end] == ",":
            end = self.skip(end + 1)
        if end >= len(text) or text[end] != close:
            raise _SyntaxError(f"Unterminated @{kind} directive.", end, "malformed_directive")
        for token in unresolved:
            self.document.diagnostics.append(
                _diag(text, "undefined_macro", f"Undefined macro: {token}.", pos, severity="warning")
            )
        if name:
            if name[0].lower() in self.macro_names:
                self.document.diagnostics.append(
                    _diag(
                        text,
                        "redefined_macro",
                        f"Macro {name[0]} is redefined; sequential scope applies.",
                        name.start(),
                        severity="warning",
                    )
                )
            self.macros[name[0].lower()] = value
            self.macro_names.add(name[0].lower())
        return end + 1

    def fields(self, entry: BibEntry, pos: int, close: str) -> int:
        text, count = self.text, 0
        while True:
            pos = self.skip(pos)
            if pos < len(text) and text[pos] == close:
                entry.close_start = pos
                return pos + 1
            count += 1
            if count > MAX_FIELDS:
                raise _SyntaxError("Entry exceeds the 256 field limit.", pos, "field_limit")
            name = _NAME.match(text, pos)
            if not name:
                raise _SyntaxError("Expected field name or closing entry delimiter.", pos)
            name_start, field_name = pos, name[0].lower()
            pos = self.skip(name.end())
            if pos >= len(text) or text[pos] != "=":
                raise _SyntaxError("Expected = after field name.", pos)
            equals, value_start = pos, self.skip(pos + 1)
            value, value_end, uses_macro, unresolved = self.expression(value_start)
            pos = self.skip(value_end)
            comma = pos if pos < len(text) and text[pos] == "," else None
            span = FieldSpan(
                field_name,
                name_start,
                name.end(),
                equals,
                value_start,
                value_end,
                comma,
                text[value_start:value_end],
                uses_macro,
            )
            if field_name in entry.fields:
                entry.editable = False
                entry.diagnostics.append(
                    _diag(
                        text,
                        "duplicate_field",
                        f"Duplicate field {field_name}; original values retained in raw entry.",
                        name_start,
                        entry.entry_id,
                    )
                )
            else:
                entry.fields[field_name], entry.field_spans[field_name] = value, span
            for token in unresolved:
                entry.diagnostics.append(
                    _diag(
                        text,
                        "undefined_macro",
                        f"Undefined macro: {token}.",
                        value_start,
                        entry.entry_id,
                        "warning",
                    )
                )
            if field_name in {"xdata", "xref"}:
                entry.editable = False
                entry.diagnostics.append(
                    _diag(
                        text,
                        "unsupported_inheritance",
                        f"{field_name} inheritance is preserved but not expanded.",
                        value_start,
                        entry.entry_id,
                        "warning",
                    )
                )
            if re.search(
                r"\\(?:input|include|write|openout|read|catcode|csname|def|directlua|usepackage)\b", value
            ):
                entry.diagnostics.append(
                    _diag(
                        text,
                        "unsafe_tex",
                        "Potentially executable TeX is preserved as inert text and is never executed.",
                        value_start,
                        entry.entry_id,
                        "warning",
                    )
                )
            if comma is not None:
                pos += 1
            elif pos >= len(text) or text[pos] != close:
                raise _SyntaxError("Expected comma between fields.", pos)

    def resolve_crossrefs(self) -> None:
        groups: dict[str, list[BibEntry]] = {}
        for entry in self.document.entries:
            if entry.key:
                groups.setdefault(entry.key, []).append(entry)
        for key, entries in groups.items():
            if len(entries) > 1:
                for entry in entries:
                    entry.editable = False
                    entry.diagnostics.append(
                        _diag(
                            self.text,
                            "duplicate_key",
                            f"Citation key {key!r} occurs {len(entries)} times; no automatic merge or edit.",
                            entry.start,
                            entry.entry_id,
                        )
                    )
        resolved: set[str] = set()

        def visit(entry: BibEntry, stack: set[str]) -> bool:
            if entry.entry_id in resolved:
                return True
            key = entry.fields.get("crossref")
            if not key:
                resolved.add(entry.entry_id)
                return True
            if entry.entry_id in stack or len(stack) >= 32:
                entry.editable = False
                entry.diagnostics.append(
                    _diag(
                        self.text,
                        "crossref_cycle",
                        "Cyclic or over-deep crossref; inheritance not resolved.",
                        entry.start,
                        entry.entry_id,
                    )
                )
                return False
            parents = groups.get(key, [])
            if len(parents) != 1:
                entry.editable = False
                code = "crossref_ambiguous" if parents else "crossref_missing"
                entry.diagnostics.append(
                    _diag(
                        self.text,
                        code,
                        f"Crossref target {key!r} is not uniquely available.",
                        entry.start,
                        entry.entry_id,
                        "warning",
                    )
                )
                return False
            parent = parents[0]
            parent.crossref_dependents.add(entry.entry_id)
            if not parent.editable or not visit(parent, stack | {entry.entry_id}):
                entry.editable = False
                entry.diagnostics.append(
                    _diag(
                        self.text,
                        "crossref_unresolved",
                        "Crossref target could not be safely resolved.",
                        entry.start,
                        entry.entry_id,
                        "warning",
                    )
                )
                return False
            for name, value in parent.fields.items():
                if name != "crossref" and name not in entry.fields:
                    entry.fields[name] = value
                    entry.inherited_fields.add(name)
            resolved.add(entry.entry_id)
            return True

        for entry in self.document.entries:
            visit(entry, set())


def _valid_key(key: str) -> bool:
    return (
        bool(key)
        and len(key) <= 512
        and not any(c.isspace() or c in ',{}()=\\"#%@' or ord(c) < 32 for c in key)
    )


def parse_bib(data: bytes | str) -> BibDocument:
    """Parse every recoverable entry; retain all input, diagnostics and offsets.

    Invalid UTF-8 is retained byte-for-byte and diagnosed, never guessed as a
    different encoding. Hard resource limits raise ``BibLimitError`` so callers
    can reject the upload explicitly rather than silently truncate it.
    """
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if not isinstance(raw, bytes):
        raise TypeError("Bib input must be bytes or text.")
    if len(raw) > MAX_BYTES:
        raise BibLimitError("Bib file exceeds the 8 MiB upload limit.")
    return _Parser(raw).parse()


def _selection_edits(
    document: BibDocument, entry: BibEntry, selection: ExportSelection
) -> tuple[list[tuple[int, int, str]], list[dict]]:
    if not entry.editable or not _valid_key(entry.key):
        raise ValueError("This entry has ambiguous or malformed source spans; keep it unchanged.")
    if entry.crossref_dependents:
        raise ValueError(
            "Other entries inherit this entry through crossref; editing it would silently change them."
        )
    if selection.proposal_type not in {"same_version_patch", "whole_version_switch"}:
        raise ValueError("Unknown proposal type.")
    if (
        not isinstance(selection.fields, Mapping)
        or (not selection.fields and not selection.entry_type)
        or len(selection.fields) > MAX_FIELDS
    ):
        raise ValueError("A bounded, nonempty field mapping is required.")
    values: dict[str, str | None] = {}
    for name, value in selection.fields.items():
        if (
            not isinstance(name, str)
            or not _NAME.fullmatch(name)
            or name.lower() in {"key", "citation_key", "entry_id", "entry_type"}
        ):
            raise ValueError("Invalid field name; citation keys cannot be changed.")
        normalized = name.lower()
        if normalized in values:
            raise ValueError("Duplicate field names in proposal.")
        if normalized in {"crossref", "xref", "xdata"}:
            raise ValueError("Changing reference inheritance is not a safe field patch.")
        if value is not None:
            _safe_value(value)
        values[normalized] = value
    target_type = selection.entry_type
    if target_type is not None and (
        not isinstance(target_type, str)
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", target_type)
        or target_type.lower() in {"comment", "string", "preamble"}
    ):
        raise ValueError("Invalid target entry type.")
    if selection.proposal_type == "whole_version_switch":
        if (
            not target_type
            or not values.get("title")
            or not values.get("year")
            or not (values.get("author") or values.get("editor"))
        ):
            raise ValueError(
                "Whole-version replacement needs an explicit type, title, year, and author or editor."
            )
        if "crossref" in entry.fields or entry.inherited_fields:
            raise ValueError(
                "Whole-version replacement of inherited fields requires a reviewed standalone record."
            )
        for name in entry.field_spans:
            if name in VERSION_FIELDS and name not in values:
                values[name] = None
    elif target_type and target_type.lower() != entry.entry_type and not selection.version_id:
        raise ValueError("Entry type correction requires a confirmed publication version.")
    edits, changes, additions = [], [], []
    for name, value in values.items():
        before = entry.fields.get(name)
        if before == value:
            continue
        if name in entry.inherited_fields:
            raise ValueError(f"Field {name} is inherited through crossref; no silent override is allowed.")
        span = entry.field_spans.get(name)
        if span and span.uses_macro:
            raise ValueError(
                f"Field {name} depends on a string macro; preserve the macro expression or edit it manually."
            )
        if span and value is None:
            # Preserve whitespace and comments surrounding each syntax token.
            edits.extend(
                [
                    (span.name_start, span.name_end, ""),
                    (span.equals, span.equals + 1, ""),
                    (span.value_start, span.value_end, ""),
                ]
            )
            if span.comma is not None:
                edits.append((span.comma, span.comma + 1, ""))
        elif span:
            edits.append((span.value_start, span.value_end, _safe_value(value)))
        elif value is not None:
            additions.append((name, value))
        else:
            continue
        changes.append(
            {
                "entry_id": entry.entry_id,
                "key": entry.key,
                "proposal_type": selection.proposal_type,
                "field": name,
                "before": before,
                "after": value,
            }
        )
    if target_type and target_type.lower() != entry.entry_type:
        edits.append((entry.type_start, entry.type_end, target_type.lower()))
        changes.append(
            {
                "entry_id": entry.entry_id,
                "key": entry.key,
                "proposal_type": selection.proposal_type,
                "field": "entry_type",
                "before": entry.entry_type,
                "after": target_type.lower(),
            }
        )
    if additions:
        surviving = [
            span
            for name, span in entry.field_spans.items()
            if values.get(name, entry.fields[name]) is not None
        ]
        if surviving:
            last = max(surviving, key=lambda span: span.value_end)
            if last.comma is None:
                edits.append((last.value_end, last.value_end, ","))
        # Empty @misc{key} has no key delimiter yet.
        elif not entry.field_spans:
            prefix = document.text[entry.type_end : entry.close_start]
            if "," not in prefix:
                edits.append((entry.close_start, entry.close_start, ","))
        newline = document.line_ending
        insertion = entry.close_start
        line_start = document.text.rfind("\n", entry.start, insertion) + 1
        if not document.text[line_start:insertion].strip():
            insertion = line_start
        leading = "" if insertion > 0 and document.text[insertion - 1] in "\r\n" else newline
        inserted = (
            leading + newline.join(f"  {name} = {_safe_value(value)}," for name, value in additions) + newline
        )
        edits.append((insertion, insertion, inserted))
    return edits, changes


def preview_export(document: BibDocument, selections: Iterable[ExportSelection | Mapping]) -> ExportPreview:
    """Produce new bytes without mutating ``document``; blocked edits are explicit.

    A proposal is atomic: an unsafe field blocks that entire proposal. Other safe
    proposals may be previewed; the HTTP service must require explicit approval
    and surface ``blocked`` before making an export artifact available.
    """
    if document.text.encode("utf-8", "surrogateescape") != document.original:
        raise ValueError("Original document integrity check failed.")
    entries = {entry.entry_id: entry for entry in document.entries}
    all_edits, changes, blocked, seen = [], [], [], set()
    materialized = list(selections)
    if len(materialized) > MAX_ENTRIES:
        raise ValueError("Too many export selections.")
    # Reject all choices for an entry selected twice, not just the second one.
    ids = [
        item.entry_id
        if isinstance(item, ExportSelection)
        else item.get("entry_id")
        if isinstance(item, Mapping)
        else None
        for item in materialized
    ]
    if any(value is not None and not isinstance(value, str) for value in ids):
        raise ValueError("Entry selection IDs must be strings.")
    duplicate_ids = {value for value, count in Counter(ids).items() if count > 1}
    for item, entry_id in zip(materialized, ids):
        try:
            if entry_id in seen:
                continue
            seen.add(entry_id)
            if entry_id in duplicate_ids:
                raise ValueError(
                    "Choose one proposal per entry; patch and whole-version selection cannot be combined."
                )
            if not isinstance(entry_id, str) or entry_id not in entries:
                raise ValueError("Selected entry does not belong to this document.")
            selection = item if isinstance(item, ExportSelection) else ExportSelection(**item)
            edits, entry_changes = _selection_edits(document, entries[entry_id], selection)
            all_edits.extend(edits)
            changes.extend(entry_changes)
        except (TypeError, ValueError) as exc:
            blocked.append({"entry_id": entry_id, "code": "unsafe_export", "message": str(exc)})
    # Coalesce insertions at one boundary: delimiter first, then new fields.
    insertions: dict[int, str] = {}
    replacements = []
    for start, end, replacement in all_edits:
        if start == end:
            insertions[start] = insertions.get(start, "") + replacement
        else:
            replacements.append((start, end, replacement))
    all_edits = replacements + [(pos, pos, value) for pos, value in insertions.items()]
    text = document.text
    # Each value span is disjoint; additions at boundaries are deliberately
    # ordered before deletion/replacement so an appended comma is not consumed.
    for start, end, replacement in sorted(all_edits, key=lambda change: (change[0], change[1]), reverse=True):
        text = text[:start] + replacement + text[end:]
    content = text.encode("utf-8", "surrogateescape")
    if changes:
        reparsed = parse_bib(content)
        changed_ids = {change["entry_id"] for change in changes}
        if len(reparsed.entries) != len(document.entries) or any(
            not entry.editable or entry.key != entries[entry.entry_id].key
            for entry in reparsed.entries
            if entry.entry_id in changed_ids
        ):
            raise ValueError("Export failed structural validation; original bytes are unchanged.")
    return ExportPreview(content, changes, blocked, hashlib.sha256(content).hexdigest())


def serialize_entry(key: str, entry_type: str, fields: Mapping[str, str]) -> str:
    """Create a safe evidence-record snippet, not a rewrite of uploaded bytes."""
    if not isinstance(key, str) or not _valid_key(key):
        raise ValueError("Invalid citation key.")
    if (
        not isinstance(entry_type, str)
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", entry_type)
        or entry_type.lower() in {"comment", "string", "preamble"}
    ):
        raise ValueError("Invalid entry type.")
    if not isinstance(fields, Mapping) or len(fields) > MAX_FIELDS:
        raise ValueError("A bounded field mapping is required.")
    lines, names = [], set()
    for name, value in fields.items():
        if (
            not isinstance(name, str)
            or not _NAME.fullmatch(name)
            or name.lower() in names
            or name.lower() in {"key", "citation_key", "entry_id", "entry_type", "crossref", "xref", "xdata"}
        ):
            raise ValueError("Invalid, duplicate or inherited field in standalone record.")
        names.add(name.lower())
        lines.append(f"  {name.lower()} = {_safe_value(value)},")
    return f"@{entry_type.lower()}{{{key},\n" + "\n".join(lines) + "\n}"
