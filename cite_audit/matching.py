"""Task-local relevance and name corroboration; similarity is never identity proof."""
from __future__ import annotations

import html
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import parse_qs, urlsplit


def plain_title(value: str) -> str:
    """Remove known publisher styling while preserving the enclosed title text."""
    return re.sub(r"</?(?:i|b|em|strong|italic|bold|jats:italic|jats:bold)\s*>",
                  "", html.unescape(value), flags=re.I)


def iclr_paper_id(url: str) -> str | None:
    match = re.fullmatch(r"https://proceedings\.iclr\.cc/paper_files/paper/(\d{4})/"
                         r"(?:hash/([a-f0-9]{32})-Abstract-Conference\.html|"
                         r"file/([a-f0-9]{32})-Paper-Conference\.pdf)", url)
    return f"{match[1]}/{match[2] or match[3]}" if match else None


def normalize(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", html.unescape(str(value or ""))).casefold()
    text = re.sub(r"\\[a-zA-Z]+", "", text)
    return re.sub(r"[^\w+#]", "", text)


def venue_normalize(value: str | None) -> str:
    text = html.unescape(str(value or "")).replace(r"\&", "&")
    return normalize(re.sub(r"&;?", " and ", text))


def names(value: str) -> list[str]:
    return [name.strip() for name in re.split(r"\s+and\s+", value or "", flags=re.IGNORECASE)
            if name.strip() and normalize(name) not in {"others", "etal"}]


def _forms(name: str) -> set[tuple[str, str, str]]:
    def tokens(text):
        return re.findall(r"[^\W_]+", unicodedata.normalize("NFKD", text).casefold())
    if "," in name:
        family, given = name.split(",", 1)
        parts = [(tokens(family), tokens(given))]
    else:
        words = tokens(name)
        # No ethnicity inference: retain both order interpretations. Neither
        # interpretation alone can confirm a person or a publication.
        parts = [(words[:1], words[1:]), (words[-1:], words[:-1])] if len(words) > 1 else []
    return {(normalize("".join(f)), normalize("".join(g)), "".join(w[0] for w in g))
            for f, g in parts if f and g}


def name_match(left: str, right: str) -> str:
    """Equivalent full names, compatible initials, or contradictory names."""
    if normalize(left) == normalize(right):
        return "initials" if re.search(r"\b[a-zA-Z]\b", left + " " + right) else "full"
    weak = False
    for lf, lg, li in _forms(left):
        for rf, rg, ri in _forms(right):
            if lf != rf:
                continue
            if lg == rg:
                return "full" if len(lg) > len(li) and len(rg) > len(ri) else "initials"
            # A single initial or explicit initials can corroborate but never
            # establish full-name equality (Y. Liu may name many people).
            weak |= ((len(lg) == 1 and rg.startswith(lg)) or
                     (len(rg) == 1 and lg.startswith(rg)) or
                     (lg == li and li == ri) or (rg == ri and ri == li))
    return "initials" if weak else "different"


def author_match(left: str, right: str) -> str:
    la, ra = names(left), names(right)
    if not la or not ra:
        return "missing"
    results = [name_match(l, r) for l, r in zip(la, ra)]
    if "different" in results:
        # Some registries reverse family/given fields for an entire romanized
        # roster. Multiple full names in the same author order corroborate this
        # interpretation; one ambiguous "Chen, Wang" never does. Keep the
        # uncertainty distinct so we do not export a guessed comma convention.
        if len(la) == len(ra) and len(la) >= 2 and all(
                name_match(l.replace(",", " "), r.replace(",", " ")) == "full" for l, r in zip(la, ra)):
            return "order_variant"
        return "different"
    truncated = bool(re.search(r"\b(?:others|et\s*al)\b", left + " " + right, re.IGNORECASE))
    if len(la) != len(ra) or truncated or "initials" in results:
        return "partial"
    return "full"


def relevance(fields: dict, record: dict) -> dict:
    """Explain relevance without trusting the input DOI or a model score."""
    other = record.get("fields", {})
    lt, rt = (normalize(plain_title(f.get("title") or "")) for f in (fields, other))
    score = SequenceMatcher(None, lt[:2000], rt[:2000], autojunk=False).ratio() if lt and rt else None
    authors = author_match(fields.get("author", ""), other.get("author", ""))
    if score is not None and score < 0.72:
        status, reason = "rejected", "标题明显不匹配，已排除搜索噪声"
    elif authors == "different" and score is not None and score < 0.95:
        status, reason = "rejected", "标题与作者均不匹配"
    elif score is None:
        status, reason = "unresolved", "缺少可比较题名，需要标识符或官方记录核实"
    elif authors == "different":
        status, reason = "unresolved", "题名相近但作者不一致，需要进一步核实"
    else:
        status, reason = "unresolved", "题名相关，尚需确认具体出版版本"
    return {"status": status, "reason": reason, "title_similarity": round(score, 3) if score is not None else None,
            "author_match": authors}


def openreview_id(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.hostname != "openreview.net" or parsed.path not in {"/forum", "/pdf"}:
        return ""
    ids = parse_qs(parsed.query).get("id", [])
    return ids[0] if len(ids) == 1 and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", ids[0]) else ""
