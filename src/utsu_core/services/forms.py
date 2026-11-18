"""Owner-scoped form contributions and submission access."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib import resources
from typing import Any

from ..contracts import FormContribution, SubmissionPolicy
from ..formdef import parse_yaml
from ..store import Doc, Store


class FormRegistry:
    def __init__(self, owners: Mapping[str, str] | None = None) -> None:
        self._owners = dict(owners or {})
        self._strict_owners = owners is not None
        self._contributions: dict[str, FormContribution] = {}
        self._policies: dict[str, SubmissionPolicy] = {}
        self._frozen = False

    def register_form(self, owner: str, form_id: str, contribution: FormContribution) -> None:
        self._ensure_mutable()
        if self._strict_owners and self._owners.get(form_id) != owner:
            raise ValueError(f"{owner} does not own form {form_id}")
        if form_id in self._contributions:
            raise ValueError(f"form {form_id} is already registered")
        parsed = parse_yaml(contribution.yaml)
        if parsed["id"] != form_id:
            raise ValueError(f"form contribution {form_id} contains definition {parsed['id']}")
        self._owners[form_id] = owner
        self._contributions[form_id] = contribution

    def register_policy(self, owner: str, form_id: str, policy: SubmissionPolicy) -> None:
        self._ensure_mutable()
        if self._owners.get(form_id) != owner:
            raise ValueError(f"{owner} does not own form {form_id}")
        if form_id in self._policies:
            raise ValueError(f"a submission policy is already registered for {form_id}")
        self._policies[form_id] = policy

    def freeze(self) -> None:
        missing = set(self._owners) - set(self._contributions)
        if missing:
            raise ValueError(f"forms declared without contributions: {', '.join(sorted(missing))}")
        self._frozen = True

    def owner(self, form_id: str) -> str | None:
        return self._owners.get(form_id)

    def policy(self, form_id: str) -> SubmissionPolicy | None:
        return self._policies.get(form_id)

    def form_ids(self, owner: str | None = None) -> tuple[str, ...]:
        return tuple(sorted(fid for fid in self._contributions
                            if owner is None or self._owners[fid] == owner))

    def contributions(self) -> tuple[tuple[str, FormContribution], ...]:
        return tuple(sorted(self._contributions.items()))

    def _ensure_mutable(self) -> None:
        if self._frozen:
            raise RuntimeError("form registry is frozen")


def register_form_resource(ctx: Any, owner: str, form_id: str, package: str) -> None:
    raw = resources.files(package).joinpath("forms", f"{form_id}.yaml").read_text(encoding="utf-8")
    ctx.form_registry.register_form(owner, form_id, FormContribution(yaml=raw))


class FormsRuntime:
    def __init__(self, store: Store, registry: FormRegistry) -> None:
        self._store = store
        self._registry = registry

    def for_owner(self, owner: str) -> "FormsView":
        return FormsView(self._store, self._registry, owner)

    def core_view(self) -> "FormsView":
        return FormsView(self._store, self._registry, None)


class FormsView:
    def __init__(self, store: Store, registry: FormRegistry, owner: str | None) -> None:
        self._store = store
        self._registry = registry
        self._owner = owner

    def submission(self, form_id: str, uid: str) -> Doc | None:
        self._check_form(form_id)
        return self._store.get("submissions", f"{form_id}:{uid}")

    def submissions(self, form_id: str, **filters: Any) -> list[Doc]:
        self._check_form(form_id)
        if "form_id" in filters:
            raise ValueError("submission form_id is controlled by the forms service")
        return self._store.list("submissions", form_id=form_id, **filters)

    def update_submission(self, form_id: str, uid: str,
                          update: Callable[[Doc | None], Doc | None]) -> Doc | None:
        self._check_form(form_id)
        if not uid or ":" in uid:
            raise ValueError("submission uid must be a non-empty account id")
        return self._store.update("submissions", f"{form_id}:{uid}", update)

    def _check_form(self, form_id: str) -> None:
        owner = self._registry.owner(form_id)
        if owner is None or (self._owner is not None and owner != self._owner):
            raise PermissionError(f"form {form_id} is outside this forms service scope")
