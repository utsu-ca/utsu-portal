"""The branding folder (theme.json, logos) is served at /branding and can be swapped without rebuilding the front end."""
import json
import re

from fastapi.testclient import TestClient
from core import create_app
from core.settings import Settings

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def test_bundled_theme_has_user_and_admin_colours_a_logo_and_a_copyright_holder():
    client = TestClient(create_app(Settings(secret="s" * 32)))
    response = client.get("/branding/theme.json")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-cache"
    theme = response.json()
    for mode, keys in (("user", {"accent", "accentHover", "chrome", "page"}), ("admin", {"accent", "accentHover", "chrome"})):
        assert keys <= set(theme["colors"][mode])
        assert all(HEX.match(theme["colors"][mode][key]) for key in keys)
    assert theme["colors"]["user"]["accent"] != theme["colors"]["admin"]["accent"]
    assert theme["copyright"]["holder"]
    for asset in (theme["logo"], theme["favicon"]):
        assert client.get(f"/branding/{asset}").status_code == 200


def test_a_custom_branding_folder_replaces_the_bundled_one(tmp_path):
    (tmp_path / "theme.json").write_text(json.dumps({"name": "Other Union", "logo": "mark.svg"}), encoding="utf-8")
    (tmp_path / "mark.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    client = TestClient(create_app(Settings(secret="s" * 32, branding_dir=str(tmp_path))))
    assert client.get("/branding/theme.json").json()["name"] == "Other Union"
    assert client.get("/branding/mark.svg").status_code == 200
    assert client.get("/branding/logo.svg").status_code == 404
