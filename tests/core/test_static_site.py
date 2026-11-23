"""The committed front-end build (src/core/frontend/ -> src/core/static) is what the server ships."""
import importlib.util
import re
import sys

import pytest
from fastapi.testclient import TestClient
from core import create_app
from core.bootstrap import STATIC
from core.settings import Settings

PAGES = ["index.html", "checkin.html", "finance.html", "sso.html"]


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(Settings(secret="s" * 32)))


@pytest.mark.parametrize("page", PAGES)
def test_each_page_and_every_asset_it_loads_is_served(client, page):
    response = client.get(f"/static/{page}")
    assert response.status_code == 200
    html = response.text
    assert "<script>" not in html and "onclick=" not in html  # the CSP forbids inline script
    assets = re.findall(r'(?:src|href)="(/static/assets/[^"]+)"', html)
    assert any(a.endswith(".js") for a in assets) and any(a.endswith(".css") for a in assets)
    for asset in assets:
        assert client.get(asset).status_code == 200, asset


def test_the_portal_root_serves_the_app(client):
    assert client.get("/").status_code == 200
    assert 'id="app"' in client.get("/").text


def test_lazy_bundle_chunks_are_served(client):
    references = {
        name
        for script in (STATIC / "assets").glob("*.js")
        for name in re.findall(r'["\'](?:assets/|\./)([\w.-]+\.(?:js|css))["\']', script.read_text(encoding="utf-8"))
    }
    assert references
    for name in references:
        assert client.get(f"/static/assets/{name}").status_code == 200, name


def test_configured_frontend_bundle_is_served(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("selected frontend", encoding="utf-8")
    (tmp_path / "asset.js").write_text("selected asset", encoding="utf-8")
    monkeypatch.setenv("PORTAL_SECRET", "s" * 32)
    monkeypatch.setenv("PORTAL_STATIC_DIR", str(tmp_path))
    client = TestClient(create_app(Settings.from_env()))
    assert client.get("/").text == "selected frontend"
    assert client.get("/static/asset.js").text == "selected asset"


def test_installed_full_frontend_is_used_unless_a_bundle_is_configured(tmp_path, monkeypatch):
    package = tmp_path / "utsu_portal_frontend"
    static = package / "static"
    static.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (static / "index.html").write_text("installed full frontend", encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "utsu_portal_frontend", package / "__init__.py", submodule_search_locations=[str(package)]
    )
    assert spec is not None
    monkeypatch.setitem(sys.modules, "utsu_portal_frontend", importlib.util.module_from_spec(spec))

    client = TestClient(create_app(Settings(secret="s" * 32)))
    assert client.get("/").text == "installed full frontend"

    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "index.html").write_text("custom frontend", encoding="utf-8")
    client = TestClient(create_app(Settings(secret="s" * 32, static_dir=str(custom))))
    assert client.get("/").text == "custom frontend"


@pytest.mark.parametrize("static_dir", ["", ".", "missing"])
def test_invalid_configured_frontend_bundle_fails_startup(tmp_path, static_dir):
    path = str(tmp_path / static_dir) if static_dir else static_dir
    with pytest.raises(RuntimeError, match="PORTAL_STATIC_DIR|missing index.html"):
        create_app(Settings(secret="s" * 32, static_dir=path))
