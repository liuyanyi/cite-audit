"""Conservative journal identity checks; ISSNs never identify articles."""
import re

from .matching import venue_normalize


def issns(value):
    tokens = value if isinstance(value, list) else re.split(r"[,;\s]+", str(value or ""))
    result = []
    for token in tokens:
        text = str(token).strip().upper().replace("-", "")
        if not re.fullmatch(r"\d{7}[\dX]", text):
            continue
        checksum = sum(int(c) * (8 - i) for i, c in enumerate(text[:7]))
        if (checksum + (10 if text[-1] == "X" else int(text[-1]))) % 11:
            continue
        canonical = text[:4] + "-" + text[4:]
        if canonical not in result:
            result.append(canonical)
    return result


def journal_matches(name, title, identifiers):
    if venue_normalize(name) == venue_normalize(title):
        return True
    # Publisher-attested name variant, never a generic prefix-removal rule.
    # https://www.techscience.com/journal/cmc
    return (bool(set(identifiers) & {"1546-2226", "1546-2218"})
            and venue_normalize(title) == venue_normalize("Computers, Materials & Continua")
            and venue_normalize(name) == venue_normalize("CMC-Computers, Materials & Continua"))
