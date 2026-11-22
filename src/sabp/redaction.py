"""Pure hardship-text redaction shared by live and historical workflows."""
import re
import unicodedata


def redact(text: str, names: list[str] = ()) -> str:
    """Remove common identifiers; the result still requires human anonymity review."""
    text = unicodedata.normalize("NFC", text or "")
    text = re.sub(r"https?://\S+", "[link]", text)
    text = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "[email]", text)
    text = re.sub(r"(?<!\d)\d{7,10}(?!\d)", "[student number]", text)
    text = re.sub(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)", "[phone]", text)
    text = re.sub(r"\b[A-Za-z]\d[A-Za-z][\s-]?\d[A-Za-z]\d\b", "[postal code]", text)

    def normalize_name(word):
        return "".join(char for char in unicodedata.normalize("NFKD", word.casefold())
                       if not unicodedata.category(char).startswith("M"))

    words = {normalize_name(w) for part in names for w in re.findall(r"\w+", unicodedata.normalize("NFC", part))}
    placeholders = {"[name]", "[email]", "[student number]", "[phone]", "[postal code]", "[link]"}

    def replace_name(match):
        word = match.group(0)
        return "[name]" if word not in placeholders and normalize_name(word) in words else word

    return re.sub(r"\[(?:name|email|student number|phone|postal code|link)\]|\w+", replace_name, text)
