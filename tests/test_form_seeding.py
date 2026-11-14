from pathlib import Path

import pytest

from utsu_core import create_app, formdef
from utsu_core.app import seed_forms
from utsu_core.settings import Settings


def test_seed_upgrade_preserves_drafts_and_never_downgrades(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "src/utsu_core/forms/student_aid_bursary.yaml").read_text()
    bundled = tmp_path / "student_aid_bursary.yaml"
    bundled.write_text(source.replace("version: 3", "version: 2", 1))
    app = create_app(Settings(secret="s" * 32, seed_forms=False))
    ctx = app.state.ctx
    seed_forms(ctx, tmp_path)
    key = "student_aid_bursary"
    draft = source.replace("version: 3", "version: 4", 1).replace("Request emergency", "Request additional")
    draft_hash = formdef.schema_hash(formdef.parse_yaml(draft))
    ctx.store.update("forms", key, lambda current: {**current, "draft_yaml": draft, "draft_hash": draft_hash,
                                                    "draft_version": 4})
    bundled.write_text(source)
    seed_forms(ctx, tmp_path)
    state = ctx.store.get("forms", key)
    assert state["published_version"] == 3
    assert state["draft_yaml"] == draft and state["draft_version"] == 4
    assert ctx.store.get("form_versions", f"{key}@3")["hash"] == formdef.schema_hash(formdef.parse_yaml(source))

    ctx.store.update("forms", key, lambda current: {**current, "published_version": 4})
    ctx.store.delete("form_versions", f"{key}@3")
    seed_forms(ctx, tmp_path)
    state = ctx.store.get("forms", key)
    assert state["published_version"] == 4 and state["draft_yaml"] == draft
    assert ctx.store.get("form_versions", f"{key}@3") is not None
    bundled.write_text(source.replace("Request emergency", "Request changed", 1))
    with pytest.raises(RuntimeError, match="definition changed"):
        seed_forms(ctx, tmp_path)
