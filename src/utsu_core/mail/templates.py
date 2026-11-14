import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
DEFAULT_TEMPLATE_DIR = "templates/email"


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class RenderedMessage:
    subject: str
    text: str
    html: str | None = None


@dataclass(frozen=True)
class Template:
    """
    A mail-merge template. Placeholders look like {{ field_name }} and are filled from a
    recipient record (a dict of column name -> value, e.g. a uoft_data row or a verified.csv row).

    >>> t = Template("demo", "Hi {{ first_name }}", "Dear {{first_name}},")
    >>> t.fields == {"first_name"}
    True
    >>> t.render({"first_name": "Ada"}).subject
    'Hi Ada'
    >>> t.missing_fields({"first_name": "  "})
    ['first_name']
    """
    name: str
    subject: str
    text: str
    html: str | None = None

    @property
    def fields(self) -> set[str]:
        parts = [self.subject, self.text, self.html or ""]
        return {m.group(1) for part in parts for m in PLACEHOLDER.finditer(part)}

    def missing_fields(self, values: Mapping[str, object]) -> list[str]:
        """Fields used by this template that are absent or blank in `values`."""
        return sorted(f for f in self.fields if not str(values.get(f) or "").strip())

    def render(self, values: Mapping[str, object]) -> RenderedMessage:
        missing = self.missing_fields(values)
        if missing:
            raise TemplateError(f"Template '{self.name}' is missing values for: {', '.join(missing)}")

        def fill(source: str, escape=None) -> str:
            def sub(m: re.Match) -> str:
                value = str(values[m.group(1)]).strip()
                return escape(value) if escape else value
            return PLACEHOLDER.sub(sub, source)

        # Subjects are header lines: collapse any whitespace/newlines a value may carry
        subject = " ".join(fill(self.subject).split())
        return RenderedMessage(subject, fill(self.text),
                               fill(self.html, html.escape) if self.html is not None else None)


def parse_template(name: str, raw_text: str, raw_html: str | None = None) -> Template:
    """
    Split a template file into its header and body. The header is `Key: value` lines,
    terminated by a blank line; only `Subject` is supported (and required).

    >>> parse_template("x", "Subject: Hello\\n\\nBody").text
    'Body\\n'
    """
    raw_text = raw_text.lstrip("\ufeff").replace("\r\n", "\n")
    head, sep, body = raw_text.partition("\n\n")
    headers = {}
    for line in head.splitlines():
        key, colon, value = line.partition(":")
        if not colon:
            raise TemplateError(f"Template '{name}': header line must look like 'Subject: ...' (got '{line}')")
        headers[key.strip().lower()] = value.strip()

    if not sep or set(headers) - {"subject"} or not headers.get("subject"):
        raise TemplateError(f"Template '{name}' must start with a 'Subject: ...' line followed by a blank line")
    return Template(name, headers["subject"], body.strip("\n") + "\n",
                    raw_html.lstrip("\ufeff") if raw_html is not None else None)


def list_templates(template_dir: Path) -> list[str]:
    return sorted(p.stem for p in Path(template_dir).glob("*.txt"))


def load_template(name: str, template_dir: Path) -> Template:
    """
    Load `<template_dir>/<name>.txt` (subject header + plain-text body) and, if present,
    `<template_dir>/<name>.html` as the HTML alternative body.
    """
    if Path(name).name != name:
        raise TemplateError(f"Invalid template name '{name}'")
    text_path = Path(template_dir) / f"{name}.txt"
    if not text_path.is_file():
        available = ", ".join(list_templates(template_dir)) or "none"
        raise TemplateError(f"Template '{name}' not found at {text_path} (available: {available})")
    html_path = text_path.with_suffix(".html")
    return parse_template(name, text_path.read_text(encoding="utf-8"),
                          html_path.read_text(encoding="utf-8") if html_path.is_file() else None)
