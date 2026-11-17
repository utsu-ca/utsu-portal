

from argparse import ArgumentParser, Namespace
import json
import logging
import os
from pathlib import Path
import sys

from utsu_core.services.text import (
    EMAIL_FLAGS, UOFT_MAIL_DOMAIN, application_uuid_for, clean_text, generate_uuid, id_kind,
    identity_key, identity_key_type, normalize_case, normalize_uoft_email, parse_id, student_uuid_for, typed_uuid,
)


logger = logging.getLogger(__name__)

def setup_logging(args):
    # Add extra logging level (once: this is also called by tests and tools in one process)
    if not hasattr(logging, 'TRACE'):
        addLoggingLevel('TRACE', logging.DEBUG - 5)

    verbosity = str(args['verbosity']).strip().upper()
    log_file = args['log_file']

    levels = {'TRACE': logging.TRACE, 'DEBUG': logging.DEBUG, 'INFO': logging.INFO,  # type: ignore
              'WARNING': logging.WARNING, 'WARN': logging.WARNING, 'ERROR': logging.ERROR,
              'CRITICAL': logging.CRITICAL}
    if verbosity not in levels:
        raise ValueError(f"--verbosity must be one of {', '.join(levels)}; got {args['verbosity']!r}")
    level = levels[verbosity]

    # Console encodings like cp1252 can't print every character in names; replace instead of raising
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    handlers = [logging.StreamHandler(sys.stdout)]
    format_string = "%(asctime)s [%(levelname)s] %(message)s"

    if log_file:
        log_path = absfile(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, mode='a', encoding='utf-8'))

    logging.basicConfig(
        level=level,
        format=format_string,
        datefmt="%H:%M:%S",
        handlers=handlers
    )


# https://stackoverflow.com/a/35804945
def addLoggingLevel(levelName, levelNum, methodName=None):
    """
    Comprehensively adds a new logging level to the `logging` module and the
    currently configured logging class.

    `levelName` becomes an attribute of the `logging` module with the value
    `levelNum`. `methodName` becomes a convenience method for both `logging`
    itself and the class returned by `logging.getLoggerClass()` (usually just
    `logging.Logger`). If `methodName` is not specified, `levelName.lower()` is
    used.

    To avoid accidental clobberings of existing attributes, this method will
    raise an `AttributeError` if the level name is already an attribute of the
    `logging` module or if the method name is already present

    Example
    -------
    >>> addLoggingLevel('TRACE', logging.DEBUG - 5)
    >>> logging.getLogger(__name__).setLevel("TRACE")
    >>> logging.getLogger(__name__).trace('that worked')
    >>> logging.trace('so did this')
    >>> logging.TRACE
    5

    """
    if not methodName:
        methodName = levelName.lower()

    if hasattr(logging, levelName):
        raise AttributeError('{} already defined in logging module'.format(levelName))
    if hasattr(logging, methodName):
        raise AttributeError('{} already defined in logging module'.format(methodName))
    if hasattr(logging.getLoggerClass(), methodName):
        raise AttributeError('{} already defined in logger class'.format(methodName))

    # This method was inspired by the answers to Stack Overflow post
    # http://stackoverflow.com/q/2183233/2988730, especially
    # http://stackoverflow.com/a/13638084/2988730
    def logForLevel(self, message, *args, **kwargs):
        if self.isEnabledFor(levelNum):
            self._log(levelNum, message, args, **kwargs)

    def logToRoot(message, *args, **kwargs):
        logging.log(levelNum, message, *args, **kwargs)

    logging.addLevelName(levelNum, levelName)
    setattr(logging, levelName, levelNum)
    setattr(logging.getLoggerClass(), methodName, logForLevel)
    setattr(logging, methodName, logToRoot)


class ConfigError(ValueError):
    """The config file exists but cannot be read as a JSON object."""


def save_config_file(path: str, cfg):
    """Save JSON config. The file is written to a temporary name and then moved into place, so a failure never leaves it half-written."""
    tmp = None
    try:
        p = absfile(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, p)
        logger.info(f"Saved configuration to {path}")
    except Exception as e:
        logger.error(f"Failed to save config to {path}: {e}")
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def load_config_file(path: str, strict: bool = False) -> dict[any, any]: # type: ignore
    """
    Load JSON. Return an object/dict. A missing file gives {}. A file that cannot be read as a JSON object gives {}
    and an error in the log, or with `strict` raises `ConfigError`, so a caller that writes the file back never
    replaces a config it could not read.
    """
    if not path:
        return {}
    p = absfile(path)
    if not p.exists():
        logger.error(f"Config file not found: {path}")
        return {}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8-sig"))
        if not isinstance(cfg, dict):
            raise ValueError(f"expected a JSON object, got {type(cfg).__name__}")
    except (OSError, ValueError) as e:
        if strict:
            raise ConfigError(f"Cannot read the config file {p}: {e}. Fix or remove it; it was left unchanged.") from e
        logger.error(f"Failed to load config file {path}: {e}")
        return {}
    logger.info(f"Loaded config: {path}")
    return cfg


def explicit_flags(parser: ArgumentParser, argv: list[str] | None = None) -> set[str]:
    """The settings (argparse `dest` names) that were actually given on the command line, even when equal to the default."""
    sentinel = object()
    probe = Namespace(**{action.dest: sentinel for action in parser._actions})
    parser.parse_args(argv, namespace=probe)
    return {key for key, value in vars(probe).items() if value is not sentinel}


def load_and_merge_config(args: Namespace, parser: ArgumentParser, transient: tuple[str, ...] = (),
                          explicit: set[str] | None = None):
    """
    CLI flags override config values. A flag counts as given when it is in `explicit` (see `explicit_flags`), even if
    its value equals the default; without `explicit`, only a value *different from* the default counts.
    `transient` flags apply to this run only: they are taken from the CLI and never saved to the config file.
    A config file that exists but cannot be read raises `ConfigError` and is left as it is (it would otherwise be
    overwritten, losing settings such as the `smtp` section).
    """
    conf = load_config_file(args.config, strict=True)

    for key, value in vars(args).items():
        # skip config-related keys
        if key in ("config", "save_config", "testing"):
            continue

        given = key in explicit if explicit is not None else value != parser.get_default(key)
        if key in transient:
            conf[key] = value
        # CLI overrides config if explicitly provided
        elif given:
            conf[key] = value
        else:
            # fallback to config value if exists, otherwise the default
            conf[key] = conf.get(key, value)

    save_config_file(args.config, {k: v for k, v in conf.items() if k not in transient})

    return conf


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
