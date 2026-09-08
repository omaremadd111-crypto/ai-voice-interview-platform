"""Conservative extraction of a name explicitly displayed near the top of a CV."""
import re

_NAME_FIELD_PATTERN = re.compile(
    r"^(?:full\s+name|name)\s*[:\-]\s*(?P<name>.+)$", re.IGNORECASE,
)
_PLAIN_NAME_PATTERN = re.compile(
    r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+"
    r"(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+){1,4}$"
)
_NON_NAME_TOKENS = {
    "analyst", "business", "candidate", "contact", "curriculum", "cv", "data",
    "demo", "developer", "engineer", "experience", "professional", "profile",
    "resume", "software", "student", "summary", "vitae",
}


def extract_explicit_candidate_name(cv_text: str) -> str | None:
    """Return only a plausible heading/name-field value from the first 15 CV lines.

    The extractor intentionally does not consider filenames, email addresses, usernames,
    or other indirect identifiers. Role headings are filtered out as well.
    """
    lines = [line.strip() for line in cv_text.splitlines() if line.strip()]
    for raw_line in lines[:15]:
        line = re.sub(r"^[#>*\-\s]+", "", raw_line).strip()
        labelled = _NAME_FIELD_PATTERN.match(line)
        candidate = labelled.group("name").strip() if labelled else line
        if not _PLAIN_NAME_PATTERN.fullmatch(candidate):
            continue
        tokens = {
            token.casefold()
            for token in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", candidate)
        }
        if tokens & _NON_NAME_TOKENS:
            continue
        return " ".join(candidate.split())
    return None
