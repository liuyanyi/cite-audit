"""Explicit manual-import formats, never misrepresented as online verification."""
from __future__ import annotations
import hashlib
import json
import re
from urllib.parse import urlsplit
from .bibtex import parse_bibtex
from .storage import now

RIS_MAP = {'TI':'title','T1':'title','PY':'year','Y1':'year','JO':'journal','JF':'journal','JA':'journal','T2':'journal','VL':'volume','IS':'number','DO':'doi','UR':'url','SN':'issn','ET':'edition','PB':'publisher','N2':'abstract'}
TEXT_MAP = {'title':'title','标题':'title','题名':'title','author':'author','作者':'author','year':'year','年份':'year','出版年':'year','journal':'journal','期刊':'journal','来源':'journal','doi':'doi','volume':'volume','卷':'volume','number':'number','期':'number','pages':'pages','页码':'pages','publisher':'publisher','出版社':'publisher','url':'url'}

def manual_record(text, source_url='', format='text'):
    if not all(isinstance(value, str) for value in (text, source_url, format)): raise ValueError('Manual text, source URL and format must be strings')
    if len(text) > 200_000: raise ValueError('Manual record exceeds 200 KB')
    if source_url:
        url = urlsplit(source_url)
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password:
            raise ValueError('Source link must be an HTTP(S) URL without credentials')
    fields, issues, entry_type = {}, [], 'misc'
    if format == 'bibtex':
        parsed = parse_bibtex(text)
        if len(parsed['entries']) != 1: raise ValueError('Import exactly one BibTeX entry')
        entry = parsed['entries'][0]
        fields, entry_type, issues = entry['fields'], entry['entry_type'], parsed['issues'] + entry.get('issues', [])
    elif format == 'ris':
        authors, start, end, seen_end = [], '', '', False
        for line in text.splitlines():
            match = re.match(r'^([A-Z0-9]{2})  -\s?(.*)$', line)
            if not match:
                if line.strip(): issues.append('Unsupported RIS continuation or malformed line retained in raw text')
                continue
            tag, value = match.groups()
            if tag == 'TY':
                if fields: raise ValueError('Import exactly one RIS record')
                entry_type = {'JOUR':'article','CONF':'inproceedings','BOOK':'book','THES':'phdthesis'}.get(value, 'misc')
            elif tag in ('AU','A1'): authors.append(value)
            elif tag == 'SP': start = value
            elif tag == 'EP': end = value
            elif tag == 'ER': seen_end = True
            elif tag in RIS_MAP:
                fields[RIS_MAP[tag]] = value[:4] if tag in ('PY','Y1') else value
        if authors: fields['author'] = ' and '.join(authors)
        if start: fields['pages'] = start + ('--' + end if end else '')
        if not seen_end: issues.append('RIS record has no ER terminator')
    elif format == 'text':
        for line in text.splitlines():
            parts = re.split(r'[:：]', line, maxsplit=1)
            if len(parts) == 2 and parts[0].strip().lower() in TEXT_MAP:
                fields[TEXT_MAP[parts[0].strip().lower()]] = parts[1].strip()
        if text.strip() and not fields: issues.append('Free-form text is retained as an unverified clue; use field:value lines for comparison')
    else: raise ValueError('Supported imports: bibtex, ris, text')
    if not fields and not text.strip() and not source_url: raise ValueError('Provide a record or source link')
    raw = {'text':text, 'format':format, 'issues':issues, 'entry_type':entry_type}
    digest = hashlib.sha256(json.dumps([source_url, raw], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
    return {'id':'manual-' + digest, 'provider':'manual', 'source_id':digest, 'url':source_url or fields.get('url',''), 'retrieved_at':now(), 'expires_at':None, 'fields':fields, 'raw':raw, 'method':'manual', 'provenance':'User-imported record; not independently verified online', 'version':'unknown', 'entry_type':entry_type}
