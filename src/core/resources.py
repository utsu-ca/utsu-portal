"""
Files that ship inside a package (SQL schemas), found through the package rather than the project folder, so a
program works from an installed copy as well as from the source tree.
"""
from importlib import resources, util
from pathlib import Path


def package_text(package: str, name: str) -> str:
    """The text of a data file in a package, e.g. `package_text("core.programs.agm", "schema.sql")`."""
    return resources.files(package).joinpath(name).read_text(encoding="utf-8-sig")


def schema_dirs(*packages: str) -> list[Path]:
    """The folders that hold `.sql` schemas: each package's own, its `schema` folder and those of its sub-packages."""
    found: list[Path] = []
    for name in packages:
        root = Path(str(resources.files(name)))
        children = sorted(p for p in root.iterdir() if p.is_dir())
        if name == "core.programs":
            relocated = {"agm": "agm", "sap": "sabp", "senate": "senate"}
            children = [
                Path(str(resources.files(relocated.get(child.name, f"{name}.{child.name}"))))
                if (child / "__init__.py").is_file()
                else child
                for child in children
                if child.name not in relocated or util.find_spec(relocated[child.name]) is not None
            ]
        found.extend(folder for folder in (root, root / "schema", *children)
                     if any(folder.glob("*.sql")))
    return list(dict.fromkeys(found))
