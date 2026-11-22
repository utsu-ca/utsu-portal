from pathlib import Path

from core.services.text import (
    EMAIL_FLAGS, UOFT_MAIL_DOMAIN, application_uuid_for, clean_text, generate_uuid, id_kind,
    identity_key, identity_key_type, normalize_case, normalize_uoft_email, parse_id, student_uuid_for, typed_uuid,
)

__all__ = [
    "EMAIL_FLAGS", "UOFT_MAIL_DOMAIN", "absfile", "application_uuid_for", "clean_text",
    "find_project_root", "generate_uuid", "id_kind", "identity_key", "identity_key_type",
    "normalize_case", "normalize_uoft_email", "parse_id", "student_uuid_for", "typed_uuid",
]


def find_project_root(path: Path | str | None = None) -> Path:
    """
    Find the project root, defined as the directory that holds `pyproject.toml` (or the `src` folder).

    Searches upward from `path` (a file or directory; defaults to this file's location), so the result
    does not depend on the current working directory.
    If no root is found, the path is returned unchanged.

    >>> find_project_root(Path("/nonexistent/place")) == Path("/nonexistent/place")
    True
    """
    start = Path(path) if path is not None else Path(__file__)
    resolved = start.resolve()
    current = resolved if resolved.is_dir() else resolved.parent

    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
        if candidate.name == "src":
            return candidate.parent
        if (candidate / "src").is_dir():
            return candidate

    return start

def absfile(relative_path: Path | str) -> Path:
    """
    Resolve a path relative to the project root into an absolute path.

    Absolute paths are returned unchanged.

    >>> absfile("data/wfni_weights.csv") == find_project_root() / "data" / "wfni_weights.csv"
    True
    """
    path = Path(relative_path)
    if path.is_absolute():
        return path
    return find_project_root() / path

if __name__ == '__main__':
    import doctest
    doctest.testmod()
