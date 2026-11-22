"""Comparison of application terms across portal and historical records."""
import unicodedata


def normalized_terms(data: object) -> set[str]:
    terms = data.get("terms") if isinstance(data, dict) else None
    if not isinstance(terms, list) or not terms or any(not isinstance(t, str) or not t.strip() for t in terms):
        raise ValueError("student aid application terms require administrator review")
    return {" ".join(unicodedata.normalize("NFKC", term).split()).casefold() for term in terms}
