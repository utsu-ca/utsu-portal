"""The committed front-end build (web/ -> src/utsu_core/static) is what the server ships, so check it is served whole."""
import re

import pytest
from fastapi.testclient import TestClient
from utsu_core import create_app
from utsu_core.settings import Settings

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
