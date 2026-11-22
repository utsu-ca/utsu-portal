"""AGM-owned verification state shared with the dependent verifier plug-in."""
from __future__ import annotations

import json
from typing import Any, Callable, Protocol

from utsu_core.contracts import PluginServices


class AgmVerificationAccess(Protocol):
    @property
    def grant_ttl(self) -> int: ...
    def rsvp(self, uuid: str) -> dict[str, Any] | None: ...
    def is_online(self, uuid: str) -> bool: ...
    def has_grant(self, uuid: str) -> bool: ...
    def pending_checks(self) -> list[dict[str, Any]]: ...
    def checks_for(self, uuid: str) -> list[dict[str, Any]]: ...
    def check(self, reference: str) -> dict[str, Any] | None: ...
    def has_media(self, reference: str) -> bool: ...
    def pending_media(self, reference: str) -> bytes | None: ...
    def save_scan(self, reference: str, uuid: str, face: str, id_card: str, submitted_at: int) -> None: ...
    def update_check(
        self, reference: str, update: Callable[[dict[str, Any] | None], dict[str, Any] | None],
    ) -> dict[str, Any] | None: ...
    def delete_media(self, reference: str) -> None: ...


class AgmVerificationState:
    def __init__(self, services: PluginServices) -> None:
        self._store = services.store
        self._forms = services.forms
        self._now = services.now
        self._grant_ttl = int(services.settings["agm_grant_ttl"])

    @property
    def grant_ttl(self) -> int:
        return self._grant_ttl

    def for_consumer(self, consumer: str) -> AgmVerificationAccess:
        if consumer != "agm_verify":
            raise PermissionError("AGM verification state is available only to agm_verify")
        return _AgmVerificationView(self)

    def rsvp(self, uuid: str) -> dict[str, Any] | None:
        submission = self._forms.submission("agm_rsvp", uuid)
        return submission if submission and submission["status"] != "rejected" else None

    def is_online(self, uuid: str) -> bool:
        submission = self.rsvp(uuid)
        return bool(submission and submission["data"].get("attendance") == "Online")

    def has_grant(self, uuid: str) -> bool:
        now = self._now()
        for check in self._store.list("agm_checks", uuid=uuid, status="approved"):
            if not check.get("decided_by"):
                continue
            if "admitted_until" in check:
                if check["admitted_until"] > now:
                    return True
            else:
                grant = self._store.get("agm_grants", uuid)
                decided_at = check.get("decided_at")
                if (
                    grant
                    and decided_at is not None
                    and decided_at <= grant.get("at", 0)
                    and min(grant.get("exp", 0), decided_at + self._grant_ttl) > now
                ):
                    return True
        return False

    def pending_checks(self) -> list[dict[str, Any]]:
        return sorted(
            self._store.list("agm_checks", status="pending"),
            key=lambda check: (check["submitted_at"], check["id"]),
        )

    def checks_for(self, uuid: str) -> list[dict[str, Any]]:
        return self._store.list("agm_checks", uuid=uuid)

    def check(self, reference: str) -> dict[str, Any] | None:
        return self._store.get("agm_checks", reference)

    def has_media(self, reference: str) -> bool:
        return self._store.get("agm_media", reference) is not None

    def pending_media(self, reference: str) -> bytes | None:
        check = self.check(reference)
        if not check or check["status"] != "pending":
            return None
        media = self._store.get("agm_media", reference)
        if media is None:
            return None
        return json.dumps({key: value for key, value in media.items() if key not in {"id", "uuid"}}).encode()

    def save_scan(self, reference: str, uuid: str, face: str, id_card: str, submitted_at: int) -> None:
        self._store.put("agm_media", reference, {"id": reference, "uuid": uuid, "face": face, "id_card": id_card})
        self._store.put(
            "agm_checks",
            reference,
            {"id": reference, "uuid": uuid, "status": "pending", "submitted_at": submitted_at},
        )

    def update_check(
        self, reference: str, update: Callable[[dict[str, Any] | None], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        return self._store.update("agm_checks", reference, update)

    def delete_media(self, reference: str) -> None:
        self._store.delete("agm_media", reference)


class _AgmVerificationView:
    def __init__(self, state: AgmVerificationState) -> None:
        self._state = state

    @property
    def grant_ttl(self) -> int:
        return self._state.grant_ttl

    def rsvp(self, uuid: str) -> dict[str, Any] | None:
        return self._state.rsvp(uuid)

    def is_online(self, uuid: str) -> bool:
        return self._state.is_online(uuid)

    def has_grant(self, uuid: str) -> bool:
        return self._state.has_grant(uuid)

    def pending_checks(self) -> list[dict[str, Any]]:
        return self._state.pending_checks()

    def checks_for(self, uuid: str) -> list[dict[str, Any]]:
        return self._state.checks_for(uuid)

    def check(self, reference: str) -> dict[str, Any] | None:
        return self._state.check(reference)

    def has_media(self, reference: str) -> bool:
        return self._state.has_media(reference)

    def pending_media(self, reference: str) -> bytes | None:
        return self._state.pending_media(reference)

    def save_scan(self, reference: str, uuid: str, face: str, id_card: str, submitted_at: int) -> None:
        self._state.save_scan(reference, uuid, face, id_card, submitted_at)

    def update_check(
        self, reference: str, update: Callable[[dict[str, Any] | None], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        return self._state.update_check(reference, update)

    def delete_media(self, reference: str) -> None:
        self._state.delete_media(reference)
