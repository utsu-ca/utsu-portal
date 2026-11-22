"""Build the split portal distributions: core, programs, branding, frontend, aggregate.

The standard wheel (pyproject.toml) is core-only. This tool builds the
program, branding, and full-frontend wheels plus the file-free aggregate
that installs them all. Alpha: breaking changes are acceptable.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
PROGRAMS = {
    "agm": ("agm", "agm_verify"),
    "sabp": ("sap",),
    "clubs": ("clubs",),
    "senate": ("senate",),
    "chat": ("discussion",),
    "voting": ("voting",),
}


def validate_frontend(directory: Path, pages: tuple[str, ...]) -> None:
    directory = directory.resolve()
    for page in pages:
        entry = directory / page
        if not entry.is_file():
            raise ValueError(f"frontend bundle is missing {page}: {directory}")
        references = re.findall(r'(?:src|href)="(/static/assets/[^"]+)"', entry.read_text(encoding="utf-8"))
        if not any(path.endswith(".js") for path in references) or not any(
            path.endswith(".css") for path in references
        ):
            raise ValueError(f"frontend entry {entry} must reference JavaScript and CSS assets")
        for reference in references:
            asset = (directory / reference.removeprefix("/static/")).resolve()
            if not asset.is_relative_to(directory) or not asset.is_file():
                raise ValueError(f"frontend entry {entry} references a missing or invalid asset: {reference}")


def build(version: str, output: Path, core_static_dir: Path | None = None) -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.glob("*.whl")):
        raise ValueError("the output directory must not already contain wheels")

    manifest_path = ROOT / "src/core/plugin_manifests/builtin.json"
    catalogue = json.loads(manifest_path.read_text(encoding="utf-8"))
    declarations = {item["id"]: item for item in catalogue["plugins"] if item.get("kind") == "program"}
    expected = {identifier for identifiers in PROGRAMS.values() for identifier in identifiers}
    if declarations.keys() != expected:
        raise ValueError("the built-in program catalogue does not match the split distribution map")
    for identifier, declaration in declarations.items():
        if declaration.get("interface") != "plugin":
            raise ValueError(f"{identifier} must use the typed plug-in interface before the split release")

    full_static_dir = (ROOT / "src/core/static").resolve()
    if core_static_dir is None:
        core_static_dir = ROOT / "src/core/frontend/.core-dist"
        if not core_static_dir.is_dir():
            subprocess.run(
                ["npm", "--prefix", str(ROOT / "src/core/frontend"), "run", "build:core"], check=True
            )
    core_static_dir = core_static_dir.resolve()
    if core_static_dir == full_static_dir or any(
        (core_static_dir / page).exists() for page in ("checkin.html", "finance.html")
    ):
        raise ValueError("the core-only frontend bundle must not be the full portal bundle")
    validate_frontend(core_static_dir, ("index.html", "sso.html"))
    validate_frontend(full_static_dir, ("index.html", "sso.html", "checkin.html", "finance.html"))

    core_name = "utsu-core"
    core_requirement = f"{core_name}=={version}"
    with tempfile.TemporaryDirectory(prefix="utsu-split-build-") as directory:
        staging = Path(directory)
        wheelhouse = staging / "wheelhouse"
        wheelhouse.mkdir()

        def copy_package(name: str, source: Path, destination: Path, *, omit_static: bool = False,
                         omit_frontend: bool = False) -> None:
            ignored_patterns = shutil.ignore_patterns("__pycache__", "*.pyc")

            def ignore(directory: str, names: list[str]) -> set[str]:
                ignored = set(ignored_patterns(directory, names))
                if Path(directory) == source:
                    if omit_static:
                        ignored.add("static")
                    if omit_frontend:  # core's UI sources and node_modules are build inputs, not wheel contents
                        ignored.add("frontend")
                return ignored

            shutil.copytree(source, destination / name, ignore=ignore)

        def wheel(name: str, packages: list[str], dependencies: list[str], *,
                  entry_points: dict[str, str] | None = None,
                  extras: dict[str, list[str]] | None = None,
                  extra_package_data: dict[str, list[str]] | None = None) -> None:
            path = staging / name
            setup = {
                "name": name,
                "version": version,
                "license_files": ["LICENSE"],
                "python_requires": project["requires-python"],
                "packages": packages,
                "install_requires": dependencies,
                "extras_require": extras or {},
                "entry_points": {"utsu.portal.plugins": [
                    f"{identifier} = {factory}" for identifier, factory in (entry_points or {}).items()
                ]},
                "package_data": {
                    "": ["*.sql", "*.svg", "*.json", "forms/*.yaml"],
                    "core": [
                        "schema/*.sql", "static/*", "static/assets/*", "plugin_manifests/*.json",
                        "mail/templates/historical/*.txt", "mail/templates/historical/*.html",
                        "mail/templates/live/*.txt",
                    ],
                    **(extra_package_data or {}),
                },
                "include_package_data": False,
            }
            shutil.copyfile(ROOT / "LICENSE", path / "LICENSE")
            (path / "setup.py").write_text(f"from setuptools import setup\nsetup(**{setup!r})\n", encoding="utf-8")
            (path / "pyproject.toml").write_text(
                '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n',
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation",
                 "--no-cache-dir", "--wheel-dir", str(wheelhouse), str(path)],
                check=True,
            )

        core = staging / core_name
        core.mkdir()
        copy_package("core", ROOT / "src/core", core, omit_static=True, omit_frontend=True)
        shutil.copytree(core_static_dir, core / "core/static")
        core_catalogue = {**catalogue, "plugins": [
            item for item in catalogue["plugins"] if item.get("kind") != "program"
        ]}
        (core / "core/plugin_manifests/builtin.json").write_text(
            json.dumps(core_catalogue, indent=2) + "\n", encoding="utf-8",
        )
        from setuptools import find_packages

        wheel(core_name, find_packages(where=str(core)), project["dependencies"],
              extras=project.get("optional-dependencies", {}))

        distribution_names = []
        for package, identifiers in PROGRAMS.items():
            name = f"utsu-program-{package}"
            distribution_names.append(name)
            path = staging / name
            path.mkdir()
            copy_package(package, ROOT / "src" / package, path)
            entries = {}
            for identifier in identifiers:
                declaration = declarations[identifier]
                entries[identifier] = declaration["factory"]
                (path / package / f"{identifier}.utsu-plugin.json").write_text(
                    json.dumps({"schema_version": 1, "plugin": declaration}, indent=2) + "\n",
                    encoding="utf-8",
                )
            dependencies = [core_requirement]
            if package == "voting":
                dependencies.append(f"utsu-program-chat=={version}")
            frontend = path / package / "frontend"
            frontend_files = [
                file.relative_to(path / package).as_posix()
                for file in frontend.rglob("*") if file.is_file()
            ]
            wheel(name, find_packages(where=str(path)), dependencies, entry_points=entries,
                  extra_package_data={package: frontend_files})

        branding_name = "utsu-branding"
        branding = staging / branding_name
        branding.mkdir()
        copy_package("branding", ROOT / "src/branding", branding)
        wheel(branding_name, ["branding"], [core_requirement])

        frontend_name = "utsu-portal-frontend"
        frontend = staging / frontend_name
        frontend.mkdir()
        frontend_package = frontend / "utsu_portal_frontend"
        frontend_package.mkdir()
        (frontend_package / "__init__.py").write_text("", encoding="utf-8")
        shutil.copytree(full_static_dir, frontend_package / "static")
        frontend_files = [
            file.relative_to(frontend_package).as_posix()
            for file in (frontend_package / "static").rglob("*") if file.is_file()
        ]
        wheel(frontend_name, ["utsu_portal_frontend"], [core_requirement],
              extra_package_data={"utsu_portal_frontend": frontend_files})

        aggregate = staging / "utsu-portal"
        aggregate.mkdir()
        wheel("utsu-portal", [], [
            core_requirement, f"{branding_name}=={version}", f"{frontend_name}=={version}",
            *(f"{name}=={version}" for name in distribution_names),
        ])

        artifacts = sorted(wheelhouse.glob("*.whl"))
        if len(artifacts) != len(PROGRAMS) + 4:
            raise RuntimeError("split release did not produce the expected set of wheels")
        owners: dict[str, str] = {}
        for artifact in artifacts:
            with ZipFile(artifact) as wheel_file:
                for member in wheel_file.namelist():
                    if member.endswith("/"):
                        continue
                    if member in owners:
                        raise RuntimeError(f"wheel path {member} is owned by both {owners[member]} and {artifact.name}")
                    owners[member] = artifact.name

        published: list[Path] = []
        complete = False
        try:
            for artifact in artifacts:
                target = output / artifact.name
                with artifact.open("rb") as source, target.open("xb") as destination:
                    published.append(target)
                    shutil.copyfileobj(source, destination)
            complete = True
        finally:
            if not complete:
                for target in published:
                    target.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="version for all split distributions")
    parser.add_argument("--out-dir", required=True, type=Path, help="directory without existing wheels")
    parser.add_argument("--core-static-dir", type=Path, default=None,
                        help="core-only frontend bundle (default: src/core/frontend/.core-dist, built if missing)")
    args = parser.parse_args()
    build(args.version, args.out_dir, args.core_static_dir)


if __name__ == "__main__":
    main()
