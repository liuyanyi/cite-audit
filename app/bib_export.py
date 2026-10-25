"""Apply only server-generated evidence changes to a new, loss-aware BibTeX file."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from fastapi import HTTPException, Request

from .bibtex import (BibTeXError, _entry_header, _identifier, _literal, _safe_value,
                     _space, parse_bibtex)

_BLOCKING = ('Unterminated', 'Malformed', 'Duplicate', 'Missing comma', 'invalid citation key',
             'Undefined', 'Cyclic', 'ambiguous', 'Ambiguous', 'not supported', 'exceeds')


def _layout(raw: str):
    header = _entry_header(raw, 0)
    if not header or raw[-1:] not in ('}', ')'):
        raise BibTeXError('Entry is not safely editable')
    _, opening = header
    comma = raw.find(',', opening + 1)
    if comma < 0:
        raise BibTeXError('Entry has no key separator')
    result, position = {}, comma + 1
    while True:
        position = _space(raw, position)
        while position < len(raw) - 1 and raw[position] == ',':
            position = _space(raw, position + 1)
        if position >= len(raw) - 1:
            break
        start = position
        name, position = _identifier(raw, position)
        name = name.lower()
        position = _space(raw, position)
        if not name or name in result or raw[position:position + 1] != '=':
            raise BibTeXError('Ambiguous field layout is not safely editable')
        value_start = _space(raw, position + 1)
        position, internal_comments = value_start, False
        while True:
            if raw[position:position + 1] in ('{', '"'):
                _, token_end = _literal(raw, position)
            else:
                token, token_end = _identifier(raw, position)
                if not token:
                    raise BibTeXError('Invalid field expression')
            following = _space(raw, token_end)
            if raw[following:following + 1] != '#':
                break
            internal_comments |= '%' in raw[token_end:following]
            position = _space(raw, following + 1)
            internal_comments |= '%' in raw[following + 1:position]
        if following < len(raw) - 1 and raw[following] != ',':
            raise BibTeXError('Missing field separator')
        result[name] = (start, value_start, token_end, following if raw[following:following + 1] == ',' else None, internal_comments)
        position = following + (raw[following:following + 1] == ',')
    return result, comma


def patch_entry(entry: dict, changes: dict, entry_type: str | None = None) -> str:
    """Preserve unmodified field expressions, comments, key and all surrounding source."""
    raw = entry['raw']
    locations, key_comma = _layout(raw)
    patches, additions = [], []
    newline = '\r\n' if '\r\n' in raw else '\n'
    if entry_type and entry_type != entry.get('entry_type'):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', entry_type):
            raise BibTeXError('Invalid entry type')
        name_end = _identifier(raw, 1)[1]
        patches.append((1, name_end, entry_type))
    for field, value in changes.items():
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_:.-]*', field):
            raise BibTeXError('Invalid field name')
        location = locations.get(field)
        if value is None:
            if location:
                start, _, end, separator, internal_comments = location
                if internal_comments:
                    raise BibTeXError('A changed field contains expression comments; retain it for manual editing')
                patches.append((start, end, ''))
                if separator is not None:
                    patches.append((separator, separator + 1, ''))
            elif field in entry.get('fields', {}):
                # Removing an inherited field cannot be expressed safely without
                # altering a shared parent or breaking this entry's crossref.
                raise BibTeXError('Cannot remove an inherited crossref field without changing its parent')
            continue
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise BibTeXError('Invalid field value')
        rendered = '{' + _safe_value(str(value), field not in {'doi', 'url', 'eprint', 'crossref'}) + '}'
        if location:
            _, start, end, _, internal_comments = location
            if internal_comments:
                raise BibTeXError('A changed field contains expression comments; retain it for manual editing')
            patches.append((start, end, rendered))
        else:
            additions.append(f'  {field} = {rendered},')
    if additions:
        remaining = [(name, location) for name, location in locations.items() if changes.get(name, '') is not None]
        if remaining:
            last = remaining[-1][1]
            if last[3] is None:
                patches.append((last[2], last[2], ','))
        # New fields go after existing comments and before the closing delimiter.
        patches.append((len(raw) - 1, len(raw) - 1, newline + newline.join(additions) + newline))
    for start, end, replacement in sorted(patches, reverse=True):
        raw = raw[:start] + replacement + raw[end:]
    return raw


def _proposal(entry: dict, choice: str):
    result = entry.get('result') or {}
    if choice == 'current':
        changes = result.get('accepted_changes')
        if changes is None:
            changes = {item['field']: item['suggested'] for item in result.get('assessments', [])
                       if item.get('judgment') in ('correction', 'completion', 'normalization')
                       and item.get('suggested') is not None and item.get('field') != 'entry_type'}
        kind = result.get('suggested_entry_type')
        assessments = {item['field']: item for item in result.get('assessments', [])}
        descriptions = [assessments.get(field, {'field': field, 'original': entry.get('fields', {}).get(field),
                         'suggested': value, 'judgment': 'completion', 'evidence_ids': [], 'reason': ''})
                        for field, value in changes.items()]
        if kind:
            descriptions.insert(0, assessments['entry_type'])
        return changes, kind, descriptions
    publication = result.get('publication_suggestion') or {}
    suggested = publication.get('suggested_bibtex')
    if not suggested:
        return {}, None, []
    parsed = parse_bibtex(suggested)['entries']
    if len(parsed) != 1:
        raise BibTeXError('Publication suggestion is not a single bibliographic entry')
    fields = parsed[0]['fields']
    original = entry.get('fields', {})
    changes = {key: fields.get(key) for key in dict.fromkeys([*original, *fields]) if original.get(key) != fields.get(key)}
    kind = parsed[0]['entry_type']
    evidence = list(dict.fromkeys(publication.get('evidence_ids', []) + publication.get('relation_evidence_ids', [])))
    descriptions = [{'field': key, 'original': original.get(key), 'suggested': value, 'judgment': 'upgrade',
                     'evidence_ids': evidence, 'reason': publication.get('warning', 'Explicit publication-version replacement')}
                    for key, value in changes.items()]
    if kind != entry.get('entry_type'):
        descriptions.insert(0, {'field': 'entry_type', 'original': entry.get('entry_type'), 'suggested': kind,
                               'judgment': 'upgrade', 'evidence_ids': evidence, 'reason': 'Explicit publication-version replacement'})
    return changes, kind, descriptions


def preview_export(task: dict, source: bytes) -> dict:
    text = source.decode('utf-8')
    source_entries = parse_bibtex(text)['entries']
    by_key = {}
    for item in source_entries:
        by_key.setdefault(item['citation_key'], []).append(item)
    output = []
    for entry in task['entries']:
        row = {'entry_id': entry['id'], 'citation_key': entry['citation_key'], 'eligible': True,
               'reason': '', 'original': entry['raw'], 'current': None, 'publication': None}
        matches = by_key.get(entry['citation_key'], [])
        problems = list(entry.get('issues', []))
        if len(matches) != 1 or matches[0]['raw'] != entry['raw']:
            row.update(eligible=False, reason='Source entry is duplicate, ambiguous or no longer matches its immutable snapshot')
        elif any(marker in problem for problem in problems for marker in _BLOCKING):
            row.update(eligible=False, reason='Ambiguous or unsupported source syntax is retained unchanged')
        elif (entry.get('result') or {}).get('freshness_state') == 'stale' or any(
                record.get('cache_state') == 'stale' for record in (entry.get('result') or {}).get('evidence', [])):
            row.update(eligible=False, reason='Historical evidence failed revalidation; keep the original until sources can be confirmed')
        elif entry.get('status') not in ('completed', 'partial'):
            row.update(eligible=False, reason='Wait for this entry to complete before exporting changes')
        else:
            for choice in ('current', 'publication'):
                try:
                    changes, kind, descriptions = _proposal(entry, choice)
                    if not descriptions:
                        continue
                    if not all(item.get('evidence_ids') for item in descriptions):
                        continue
                    row[choice] = {'bibtex': patch_entry(entry, changes, kind), 'changes': descriptions}
                except BibTeXError as exc:
                    row['reason'] = str(exc)
            if not row['current'] and not row['publication']:
                row.update(eligible=False, reason=row['reason'] or 'No evidence-backed changes are available')
        output.append(row)
    warnings = ['Original files are never overwritten. Unselected and unresolved entries remain unchanged.']
    if task.get('mode') == 'mock':
        warnings.append('Offline demonstration evidence is synthetic; do not use this export to revise a real bibliography.')
    return {'task_id': task['id'], 'filename': task['filename'], 'entries': output, 'warnings': warnings}


def export_bib(task: dict, source: bytes, selections: list) -> dict:
    preview = preview_export(task, source)
    entries = {entry['id']: entry for entry in task['entries']}
    previews = {row['entry_id']: row for row in preview['entries']}
    text = source.decode('utf-8')
    sources = {item['citation_key']: item for item in parse_bibtex(text)['entries']}
    seen, patches, report = set(), [], []
    if not isinstance(selections, list) or len(selections) > len(entries):
        raise BibTeXError('Selections must be a bounded list of task entries')
    for selection in selections:
        if not isinstance(selection, dict) or set(selection) - {'entry_id', 'choice', 'fields'}:
            raise BibTeXError('Invalid export selection')
        identity, choice = selection.get('entry_id'), selection.get('choice')
        if not isinstance(identity, str) or identity in seen or identity not in entries or choice not in ('current', 'publication'):
            raise BibTeXError('Unknown, duplicate or invalid export selection')
        seen.add(identity)
        row = previews[identity]
        if not row['eligible'] or not row.get(choice):
            raise BibTeXError('The selected entry has no safely applicable evidence-backed proposal')
        entry = entries[identity]
        changes, kind, descriptions = _proposal(entry, choice)
        selected_fields = selection.get('fields')
        if selected_fields is not None:
            if choice != 'current' or not isinstance(selected_fields, list) or any(not isinstance(field, str) for field in selected_fields):
                raise BibTeXError('Field selection is only supported for current-version changes')
            allowed = {item['field'] for item in descriptions}
            chosen = set(selected_fields)
            if not chosen.issubset(allowed):
                raise BibTeXError('Unknown selected field')
            required = set(entry.get('result', {}).get('type_dependencies', [])) & allowed
            if chosen & required and not required.issubset(chosen):
                raise BibTeXError('Entry type and its venue corrections must be adopted together')
            changes = {key: value for key, value in changes.items() if key in chosen}
            if 'entry_type' not in chosen:
                kind = None
            descriptions = [item for item in descriptions if item['field'] in chosen]
        if not descriptions:
            continue
        original = sources[entry['citation_key']]
        patched = patch_entry(entry, changes, kind)
        patches.append((original['source_start'], original['source_end'], patched))
        report.append({'entry_id': identity, 'citation_key': entry['citation_key'], 'choice': choice, 'changes': descriptions})
    for start, end, replacement in sorted(patches, reverse=True):
        text = text[:start] + replacement + text[end:]
    checked = parse_bibtex(text)['entries']
    original_entries = parse_bibtex(source.decode('utf-8'))['entries']
    if [item['citation_key'] for item in checked] != [item['citation_key'] for item in original_entries]:
        raise BibTeXError('Export validation changed entry count or citation keys; no file was generated')
    filename = Path(task['filename']).stem + '.optimized.bib'
    return {'filename': filename, 'bibtex': text, 'changes': report, 'warnings': preview['warnings'],
            'source_sha256': hashlib.sha256(source).hexdigest()}


def register_export_routes(app, db, config):
    def context(task_id):
        task, source = db.task(task_id, True), db.source(task_id)
        if not task or not source:
            raise HTTPException(404, 'Task not found')
        return task, source['content']

    @app.get('/api/tasks/{task_id}/export-preview')
    def preview(task_id: str):
        task, source = context(task_id)
        return preview_export(task, source)

    @app.post('/api/tasks/{task_id}/export')
    async def export(task_id: str, request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {'selections'}:
                raise BibTeXError('Provide only the selections list')
            task, source = context(task_id)
            # Source content is a user-owned download, like /source. Do not redact
            # or otherwise alter its unchanged bytes; saved assessment evidence
            # has already been sanitized before persistence.
            return export_bib(task, source, body['selections'])
        except (ValueError, UnicodeDecodeError, BibTeXError) as exc:
            raise HTTPException(422, str(exc)) from None
