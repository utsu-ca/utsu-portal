"""Permission identifiers use OAuth scope-token syntax so they can be exposed safely in OAuth flows later."""
from __future__ import annotations

import re
from collections.abc import Iterable

from .contracts import PluginManifest
from .role_catalogue import MEMBER_ROLES

_OAUTH_SCOPE_TOKEN = re.compile(r"[\x21\x23-\x5B\x5D-\x7E]+\Z")

PERM_IDENTITY_VERIFICATION_REVIEW = "utsu:identity:verification:review"
PERM_FORMS_MANAGE = "utsu:forms:manage"
PERM_AUDIT_READ = "utsu:audit:read"
PERM_PAYMENTS_MANAGE = "utsu:payments:orders:manage"
PERM_PROFILE_FINANCE = "utsu:profile:financial:manage"
PERM_FEES_MANAGE = "utsu:fees:manage"
PERM_FEES_REFUNDS = "utsu:fees:refunds:manage"
PERM_FEES_LEVY = "utsu:fees:levy:view"
PERM_CLUB_MEMBERS_MANAGE = "utsu:clubs:members:manage"
PERM_CLUB_VOTE = "utsu:clubs:vote"
PERM_CLUB_DISCUSS = "utsu:clubs:discuss"
PERM_CLUB_BALLOTS_MANAGE = "utsu:clubs:ballots:manage"
PERM_AGM_RSVP = "utsu:agm:rsvp"
PERM_AGM_ONLINE = "utsu:agm:online:attend"
PERM_SENATE_APPLY = "utsu:senate:apply"
PERM_SAP_APPLY = "utsu:sap:apply"
PERM_DISCUSSION_PARTICIPATE = "utsu:discussion:participate"
PERM_VOTING_VOTE = "utsu:voting:vote"
PERM_AGM_CHECKIN = "utsu:agm:checkin:manage"
PERM_AGM_REGISTRATIONS = "utsu:agm:registrations:manage"
PERM_AGM_ID_REVIEW = "utsu:agm:identity:review"
PERM_SENATE_APPLICATIONS = "utsu:senate:applications:manage"
PERM_SAP_APPLICATIONS = "utsu:sap:applications:manage"
PERM_SAP_FINANCE = "utsu:sap:banking:manage"
PERM_SAP_REVIEW = "utsu:sap:reviews:manage"
PERM_CLUBS_MANAGE = "utsu:clubs:applications:manage"
PERM_CLUBS_FINANCE = "utsu:clubs:finance:manage"
PERM_DISCUSSION_MODERATE = "utsu:discussion:moderate"
PERM_ELECTIONS_ADMIN = "utsu:voting:elections:manage"

CORE_PERMISSIONS = {
    PERM_IDENTITY_VERIFICATION_REVIEW: "Review identity verifications.",
    PERM_FORMS_MANAGE: "Create, edit, publish, and administer forms.",
    PERM_AUDIT_READ: "Read, verify, and export the audit log.",
}

CORE_ROLE_PERMISSIONS = {
    "staff": frozenset(),
    "admin": frozenset(),
    "verifier": frozenset({PERM_IDENTITY_VERIFICATION_REVIEW}),
    "forms_editor": frozenset({PERM_FORMS_MANAGE}),
    "auditor": frozenset({PERM_AUDIT_READ}),
}


def is_oauth_scope_token(value: str) -> bool:
    """Return whether a permission identifier is a single RFC 6749 scope-token."""
    return bool(_OAUTH_SCOPE_TOKEN.fullmatch(value))


def permission_catalogue_for(manifests: Iterable[PluginManifest]) -> dict[str, str]:
    catalogue = dict(CORE_PERMISSIONS)
    for manifest in manifests:
        for scope, description in manifest.permissions:
            if scope in catalogue:
                raise RuntimeError(f"permission {scope} is declared more than once")
            catalogue[scope] = description
    return catalogue


def role_permissions_for(manifests: Iterable[PluginManifest]) -> dict[str, frozenset[str]]:
    grants = dict(CORE_ROLE_PERMISSIONS)
    owners = {role: "core" for role in grants}
    for manifest in manifests:
        for role, scopes in manifest.role_permissions:
            if role in MEMBER_ROLES:  # every program may grant its own permissions to a membership role
                grants[role] = grants.get(role, frozenset()) | frozenset(scopes)
                continue
            if role in owners:
                raise RuntimeError(f"role permissions for {role} are declared by both {owners[role]} and {manifest.id}")
            grants[role] = frozenset(scopes)
            owners[role] = manifest.id
    return grants
