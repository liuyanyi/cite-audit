"""Lossless source snapshots and a small, real BibTeX state-machine parser.

The parser never evaluates TeX. Expressions, balanced braced/quoted values,
concatenation, string macros and crossrefs are parsed before semantic values are
resolved. Recovery is deliberately entry-local so a broken entry cannot hide the
rest of an upload. Raw source is kept separately from normalized comparison data.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass

MAX_SOURCE_CHARS = 8_000_000
MAX_ENTRIES = 20_000
MAX_DEPTH = 128
MAX_VALUE_CHARS = 1_000_000
MAX_TOTAL_VALUE_CHARS = 16_000_000
_MONTHS = dict(zip(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
                   ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")))


class BibTeXError(ValueError):
    """An input limit or local syntax error, never an instruction to execute TeX."""


@dataclass
class _Token:
    value: str
    macro: bool = False


def _space(text: str, pos: int) -> int:
    """Skip whitespace and comments between syntactic tokens."""
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
        elif text[pos] == "%":
            end = text.find("\n", pos)
            pos = len(text) if end < 0 else end + 1
        else:
            break
    return pos


def _identifier(text: str, pos: int) -> tuple[str, int]:
    start = pos
    while pos < len(text) and (text[pos].isalnum() or text[pos] in "_-:.+/"):
        pos += 1
    return text[start:pos], pos


def _entry_header(text: str, pos: int) -> tuple[str, int] | None:
    if pos >= len(text) or text[pos] != "@":
        return None
    name, end = _identifier(text, pos + 1)
    end = _space(text, end)
    if name and end < len(text) and text[end] in "{(":
        return name.lower(), end
    return None


def _line_entry(text: str, pos: int) -> bool:
    if _entry_header(text, pos) is None:
        return False
    previous = text.rfind("\n", 0, pos)
    return not text[previous + 1:pos].strip()


def _block_end(text: str, opening: int, budget: list[int]) -> tuple[int, bool]:
    """Find a balanced entry end, or a conservative next-entry recovery point."""
    closing = "}" if text[opening] == "{" else ")"
    depth = 0
    quote = False
    i = opening + 1
    recovery = None
    line_clean = False
    while i < len(text):
        budget[0] -= 1
        if budget[0] < 0:
            raise BibTeXError("Parser scan budget exceeded while recovering malformed entries")
        char = text[i]
        if char == "\\":
            line_clean = False
            i += 2
            continue
        if char == "@" and line_clean and _entry_header(text, i) is not None:
            # A header at field level is invalid. Inside a literal it may be
            # intentional text: recover there only if balancing later fails.
            if depth == 0 and not quote:
                return i, False
            if recovery is None:
                recovery = i
        if char in "\r\n":
            line_clean = True
        elif not char.isspace():
            line_clean = False
        if char == '"' and depth == 0:
            quote = not quote
        elif char == "{" :
            depth += 1
            if depth > MAX_DEPTH:
                raise BibTeXError(f"Brace nesting exceeds {MAX_DEPTH} levels")
        elif char == "}" and depth:
            depth -= 1
        elif char == closing and depth == 0 and not quote:
            return i + 1, True
        elif char == "%" and depth == 0 and not quote:
            end = text.find("\n", i)
            if end < 0:
                return len(text), False
            i = end
        i += 1
    return recovery if recovery is not None else len(text), False


def _literal(text: str, pos: int) -> tuple[str, int]:
    opener = text[pos]
    ender = "}" if opener == "{" else '"'
    pos += 1
    start = pos
    depth = 0
    while pos < len(text):
        char = text[pos]
        if char == "\\":
            pos += 2
            continue
        if char == "{" :
            depth += 1
            if depth > MAX_DEPTH:
                raise BibTeXError(f"Brace nesting exceeds {MAX_DEPTH} levels")
        elif char == "}" and depth:
            depth -= 1
        elif char == ender and depth == 0:
            return text[start:pos], pos + 1
        pos += 1
    raise BibTeXError("Unterminated braced or quoted field value")


def _expression(text: str, pos: int) -> tuple[list[_Token], int]:
    parts: list[_Token] = []
    while True:
        pos = _space(text, pos)
        if pos >= len(text) or text[pos] in ",}":
            raise BibTeXError("Missing field value")
        if text[pos] in '{"':
            value, pos = _literal(text, pos)
            parts.append(_Token(value))
        else:
            name, end = _identifier(text, pos)
            if not name:
                raise BibTeXError(f"Unexpected character {text[pos]!r} in field value")
            parts.append(_Token(name, not name.isdigit()))
            pos = end
        pos = _space(text, pos)
        if pos < len(text) and text[pos] == "#":
            pos += 1
        else:
            return parts, pos


def _recover_field(text: str, pos: int) -> int:
    """Recover at a comma not nested in a correctly balanced literal."""
    while pos < len(text):
        if text[pos] == ",":
            return pos + 1
        if text[pos] in '{"':
            try:
                _, pos = _literal(text, pos)
                continue
            except BibTeXError:
                return len(text)
        pos += 1
    return pos


def _fields(body: str) -> tuple[dict[str, list[_Token]], list[str]]:
    fields: dict[str, list[_Token]] = {}
    issues: list[str] = []
    pos = 0
    while True:
        pos = _space(body, pos)
        while pos < len(body) and body[pos] == ",":
            pos = _space(body, pos + 1)
        if pos >= len(body):
            break
        start = pos
        name, pos = _identifier(body, pos)
        name = name.lower()
        pos = _space(body, pos)
        if not name or pos >= len(body) or body[pos] != "=":
            issues.append(f"Malformed field near {body[start:start + 40]!r}; expected name = value")
            pos = _recover_field(body, max(pos, start + 1))
            continue
        try:
            expression, pos = _expression(body, pos + 1)
            if name in fields:
                issues.append(f"Duplicate field {name!r}; the first value is retained")
            else:
                fields[name] = expression
            after = _space(body, pos)
            if after < len(body) and body[after] != ",":
                issues.append(f"Missing comma after field {name!r}")
            pos = after
        except BibTeXError as error:
            issues.append(f"Field {name!r}: {error}")
            pos = _recover_field(body, pos + 1)
    return fields, issues


def semantic_fingerprint(entry: dict) -> str:
    """Hash all semantic fields, not citation keys, source formatting or order."""
    payload = {
        "entry_type": str(entry.get("entry_type", "")).lower(),
        "fields": {str(k).lower(): " ".join(unicodedata.normalize("NFC", str(v)).split())
                   for k, v in sorted(entry.get("fields", {}).items())},
    }
    # Ambiguous or unsupported syntax has no reliable semantic normalization.
    # Include its raw snapshot rather than accidentally reusing an old verdict.
    if any(any(marker in issue for marker in ("Unterminated", "Malformed", "Duplicate field", "Missing comma", "Undefined", "Cyclic", "not supported", "ambiguous", "Ambiguous", "exceeds"))
           for issue in entry.get("issues", [])):
        payload["unresolved_source"] = hashlib.sha256(entry.get("raw", "").encode("utf-8")).hexdigest()
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def parse_bibtex(text: str) -> dict:
    """Parse every recoverable entry while preserving its exact original slice.

    Unknown macros remain visible and are reported. Crossrefs are resolved only
    when unambiguous; inherited fields participate in the fingerprint. Duplicate
    keys and potential duplicate works are retained, never silently overwritten.
    """
    if not isinstance(text, str):
        raise TypeError("BibTeX input must be text")
    if len(text) > MAX_SOURCE_CHARS:
        raise BibTeXError(f"BibTeX source exceeds {MAX_SOURCE_CHARS} characters")
    entries: list[dict] = []
    issues: list[str] = []
    macros: dict[str, list[_Token]] = {name: [_Token(value)] for name, value in _MONTHS.items()}
    defined_macros: set[str] = set()
    ambiguous_macros: set[str] = set()
    scan_budget = [len(text) * 4 + 1000]
    pos = 0
    while pos < len(text):
        pos = _space(text, pos)
        if pos >= len(text):
            break
        if text[pos] != "@":
            pos += 1  # BibTeX permits free text outside entries.
            continue
        start = pos
        header = _entry_header(text, pos)
        if not header:
            issues.append(f"Invalid entry header at character {pos}")
            pos += 1
            continue
        entry_type, opening = header
        try:
            end, closed = _block_end(text, opening, scan_budget)
        except BibTeXError as error:
            if "scan budget" in str(error):
                raise
            issues.append(f"Entry at character {start}: {error}")
            end = opening + 1
            while end < len(text) and not (text[end] == "@" and _line_entry(text, end)):
                end += 1
            closed = False
        body = text[opening + 1:end - 1 if closed else end]
        pos = end
        local_issues = [] if closed else ["Unterminated entry; recovered at the next entry or end of input"]
        if entry_type == "comment":
            issues.extend(local_issues)
            continue
        if entry_type == "preamble":
            try:
                _expression(body, 0)
            except BibTeXError as error:
                local_issues.append(f"Preamble: {error}")
            issues.extend(local_issues)
            continue
        if entry_type == "string":
            definitions, macro_issues = _fields(body)
            for name, definition in definitions.items():
                if name in defined_macros and macros.get(name) != definition:
                    ambiguous_macros.add(name)
                    macro_issues.append(f"Ambiguous redefinition of string macro {name!r}; references remain unresolved")
                defined_macros.add(name)
            macros.update(definitions)
            issues.extend(local_issues + [f"String macro: {value}" for value in macro_issues])
            continue
        split = 0
        while split < len(body) and body[split] != ",":
            if body[split] == "=" or body[split] in '{"':
                # A missing key comma should not absorb the first field.
                newline = body.rfind("\n", 0, split)
                split = newline if newline >= 0 else split
                break
            split += 1
        key = body[:split].strip()
        if split >= len(body) or body[split] != ",":
            local_issues.append("Missing comma after citation key")
        if not key or any(char.isspace() or char in "{}()=\"" for char in key):
            local_issues.append("Missing or invalid citation key")
        field_tokens, field_issues = _fields(body[split + 1:])
        entry = {"citation_key": key, "entry_type": entry_type, "fields": {},
                 "raw": text[start:end], "source_start": start, "source_end": end, "issues": local_issues + field_issues,
                 "_tokens": field_tokens}
        entries.append(entry)
        if len(entries) > MAX_ENTRIES:
            raise BibTeXError(f"BibTeX source exceeds {MAX_ENTRIES} entries")

    macro_cache: dict[str, str] = {}
    total_values = 0
    def resolve(parts: list[_Token], problems: list[str], stack: tuple[str, ...] = ()) -> str:
        values = []
        length = 0
        for part in parts:
            if not part.macro:
                value = part.value
            else:
                name = part.value.lower()
                if name in ambiguous_macros:
                    problems.append(f"Ambiguous redefinition of string macro {part.value!r}; value remains unresolved")
                    value = part.value
                elif name not in macros:
                    problems.append(f"Undefined string macro {part.value!r}")
                    value = part.value
                elif name in stack or len(stack) >= MAX_DEPTH:
                    problems.append(f"Cyclic or excessively nested string macro {part.value!r}")
                    value = part.value
                elif name in macro_cache:
                    value = macro_cache[name]
                else:
                    count = len(problems)
                    value = resolve(macros[name], problems, stack + (name,))
                    if len(problems) == count:
                        macro_cache[name] = value
            length += len(value)
            if length > MAX_VALUE_CHARS:
                problems.append(f"Expanded field exceeds {MAX_VALUE_CHARS} characters; value was not expanded")
                return "[macro expansion limit exceeded]"
            values.append(value)
        return "".join(values)

    by_key: dict[str, list[dict]] = {}
    for entry in entries:
        entry["fields"] = {name: resolve(parts, entry["issues"]) for name, parts in entry.pop("_tokens").items()}
        total_values += sum(len(value) for value in entry["fields"].values())
        if total_values > MAX_TOTAL_VALUE_CHARS:
            raise BibTeXError(f"Total expanded values exceed {MAX_TOTAL_VALUE_CHARS} characters")
        by_key.setdefault(entry["citation_key"], []).append(entry)
    for key, group in by_key.items():
        if len(group) > 1:
            for entry in group:
                entry["issues"].append(f"Duplicate citation key {key!r}")

    resolved: set[int] = set()
    def inherit(entry: dict, ancestors: set[int]) -> None:
        token = id(entry)
        if token in resolved:
            return
        if token in ancestors or len(ancestors) >= MAX_DEPTH:
            entry["issues"].append("Cyclic crossref or crossref depth limit exceeded")
            return
        reference = entry["fields"].get("crossref")
        if reference:
            parents = by_key.get(reference, [])
            if len(parents) != 1:
                entry["issues"].append(f"Crossref {reference!r} is missing or ambiguous")
            elif id(parents[0]) in ancestors | {token}:
                entry["issues"].append(f"Cyclic crossref {reference!r}")
            else:
                parent = parents[0]
                inherit(parent, ancestors | {token})
                if not any("Cyclic crossref" in value for value in parent["issues"]):
                    inherited = {}
                    for name, value in parent["fields"].items():
                        if name in {"crossref", "title", "author"}:
                            continue
                        inherited[name] = value
                    if entry["entry_type"] in {"inproceedings", "conference", "incollection", "inbook"}:
                        inherited.setdefault("booktitle", parent["fields"].get("title", ""))
                    entry["fields"] = {**inherited, **entry["fields"]}
                else:
                    entry["issues"].append(f"Cyclic crossref {reference!r}")
        resolved.add(token)

    works: dict[tuple[str, str], list[dict]] = {}
    inherited_total = 0
    for entry in entries:
        inherit(entry, set())
        fields = entry["fields"]
        inherited_total += sum(len(value) for value in fields.values())
        if inherited_total > MAX_TOTAL_VALUE_CHARS:
            raise BibTeXError(f"Total inherited values exceed {MAX_TOTAL_VALUE_CHARS} characters")
        for required in ("title", "author", "year"):
            if not fields.get(required, "").strip():
                # Editors legitimately substitute for authors on many collections.
                if required == "author" and fields.get("editor"):
                    continue
                entry["issues"].append(f"Missing or empty {required} field")
        if fields.get("xdata"):
            entry["issues"].append("BibLaTeX xdata inheritance is not supported; xdata is preserved")
        entry["is_chinese"] = any("\u3400" <= char <= "\u9fff" for char in " ".join(fields.values()))
        entry["fingerprint"] = semantic_fingerprint(entry)
        signature = (" ".join(fields.get("title", "").casefold().split()),
                     " ".join(fields.get("author", "").casefold().split()))
        if signature[0] and signature[1]:
            works.setdefault(signature, []).append(entry)
        entry["issues"] = list(dict.fromkeys(entry["issues"]))
    for group in works.values():
        if len(group) > 1:
            for entry in group:
                other_keys = [other["citation_key"] for other in group if other is not entry]
                entry["issues"].append("Potential duplicate work: " + ", ".join(other_keys))
    for entry in entries:
        issues.extend(f"{entry['citation_key'] or '(missing key)'}: {item}" for item in entry["issues"])
    return {"entries": entries, "issues": issues}


def _safe_value(value: str, escape_specials: bool) -> str:
    """Preserve existing TeX and protected case; neutralize unbalanced braces."""
    result = []
    open_positions = []
    pos = 0
    while pos < len(value):
        char = value[pos]
        if char == "\\" and pos + 1 < len(value):
            result.extend((char, value[pos + 1]))
            pos += 2
            continue
        if char == "{":
            open_positions.append(len(result))
            result.append(char)
        elif char == "}":
            if open_positions:
                open_positions.pop()
                result.append(char)
            else:
                result.append("\\}")
        elif escape_specials and char in "%&#_":
            result.append("\\" + char)
        elif char == "\\":
            result.append("\\textbackslash{}")
        else:
            result.append(char)
        pos += 1
    for index in open_positions:
        result[index] = "\\{"
    return "".join(result)


def render_suggestion(entry: dict, changes: dict, entry_type: str | None = None) -> str:
    """Return a standalone suggestion without changing the input or source file.

    None deletes a field; all other changed values must be scalar strings or
    numbers. Citation keys and untouched custom/TeX fields are preserved.
    """
    kind = entry_type or entry.get("entry_type", "misc")
    key = str(entry.get("citation_key", ""))
    if not isinstance(kind, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", kind):
        raise BibTeXError("Invalid suggested entry type")
    if not key or any(char.isspace() or char in '{},()=\"' for char in key):
        raise BibTeXError("Cannot render a suggestion with an invalid citation key")
    fields = dict(entry.get("fields", {}))
    for name, value in changes.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_:.-]*", name):
            raise BibTeXError("Invalid suggested field name")
        name = name.lower()
        if value is None:
            fields.pop(name, None)
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            fields[name] = str(value)
        else:
            raise BibTeXError(f"Invalid suggested value for {name}")
    lines = [f"@{kind}{{{key},"]
    changed_names = {str(name).lower() for name in changes}
    for name, value in fields.items():
        # Identifiers and URLs retain their exact characters inside braces.
        safe = _safe_value(str(value), name in changed_names and name not in {"doi", "url", "eprint", "crossref"})
        lines.append(f"  {name} = {{{safe}}},")
    lines.append("}")
    return "\n".join(lines)
