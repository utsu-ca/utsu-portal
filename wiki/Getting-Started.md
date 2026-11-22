# Getting Started

> Historical batch documentation: the commands and setup below no longer describe the web application. See
> [README](../README.md) for current setup and [Applicant Portal](Applicant-Portal.md) for its workflows.

## Requirements

- Python **3.12 or newer**. There are no runtime dependencies. `pytest` is the only optional (dev) dependency.
- The package must be installed **editable** (see below). The code finds `data/`, `templates/`,
  `secrets/` and `testing/` by walking up to the project root (the folder with `pyproject.toml`), so a regular
  (non-editable) install into `site-packages` will not find them. (The SQL schemas ship inside the packages.)

## Install

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

With [uv](https://docs.astral.sh/uv/) instead: `uv run --extra dev python -m pytest` creates the environment on demand.

The editable install puts `utsu_core` on the import path. Relative paths are always resolved from the
project root, never from the current directory. For portal setup, see [README](../README.md).

## Synthetic data

`python testing/generate_fake_data.py` generates a fake roster and fake Senate, SABP and AGM exports for testing
(see [Testing and Development](Testing-and-Development#fake-data)). Generated CSVs are not portal import files.

For portal workflows, see [README](../README.md) and the [Operations Runbook](Operations-Runbook).

## Running the tests

```powershell
python -m pytest                          # 179 tests in tests/
python -m pytest --doctest-modules src    # 39 doctests embedded in the source
```
