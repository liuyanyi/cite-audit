"""Known-identifier ACL Anthology metadata, never a Scholar scraper."""
from __future__ import annotations
import re
from urllib.parse import unquote, urlsplit

from .bibtex import parse_bibtex

_ID = re.compile(r'(?:[A-Za-z]\d{2}-\d{4}|\d{4}\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.\d+)')


def anthology_id(fields: dict) -> str:
    """Only explicit ACL URLs, ACL DOI suffixes or an anthology_id authorize a lookup."""
    for key in ('anthology_id', 'url', 'doi'):
        value = unquote(str(fields.get(key, '')).strip())
        if key == 'url':
            parsed = urlsplit(value)
            if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('aclanthology.org', 'www.aclanthology.org'):
                continue
            value = parsed.path.strip('/').removesuffix('.bib').removesuffix('.pdf')
        elif key == 'doi':
            value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value, flags=re.I)
            if not value.lower().startswith('10.18653/v1/'):
                continue
            value = value[len('10.18653/v1/'):]
        if _ID.fullmatch(value):
            return value
    return ''


def collect_acl(fields: dict, request, make_record) -> list[dict]:
    identity = anthology_id(fields)
    if not identity:
        return []
    url = f'https://aclanthology.org/{identity}/'
    envelope = request('acl', 'anthology_bibtex', f'https://aclanthology.org/{identity}.bib',
                       text=True, headers={'Accept': 'application/x-bibtex'}, kind='metadata')
    text = envelope.get('data')
    if not isinstance(text, str):
        raise ValueError('ACL BibTeX response must be text')
    parsed = parse_bibtex(text)
    if len(parsed['entries']) != 1:
        raise ValueError('ACL single-record endpoint returned an ambiguous bibliography')
    entry = parsed['entries'][0]
    if any(any(marker in problem for marker in ('Malformed', 'Unterminated', 'Duplicate', 'Missing comma')) for problem in entry['issues']):
        raise ValueError('ACL BibTeX response is malformed')
    result = dict(entry['fields'])
    # A mismatched endpoint record is not transformed into evidence for the requested ID.
    returned = anthology_id(result)
    if returned and returned.lower() != identity.lower():
        raise ValueError('ACL record identifier does not match the requested publication')
    result['entrytype'] = entry['entry_type']
    result.setdefault('url', url)
    return [make_record('acl', identity, url, result, {'bibtex': text, 'anthology_id': identity}, envelope,
                        version='published', provenance='acl_anthology_bibtex')]
