"""Nightly club-receipt migration and cold-storage maintenance."""
from __future__ import annotations

import argparse
import json
import logging

from . import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migrate", action="store_true", help="externalize legacy receipts to encrypted hot storage")
    parser.add_argument("--apply", action="store_true", help="archive candidates (requires enabled setting)")
    parser.add_argument("--scheduled", action="store_true", help="apply only if the archive-enabled setting is set")
    parser.add_argument("--reconcile-owner", help="rebuild a user:UUID or club:ID quota; stop writers first")
    parser.add_argument("--quota-scope", choices=("auth", "portal", "all"), default="all")
    parser.add_argument("--auth-service", action="store_true", help="reconcile the standalone auth store")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.auth_service:
        if not args.reconcile_owner or args.quota_scope != "auth":
            parser.error("--auth-service requires --reconcile-owner and --quota-scope auth")
        from .authsvc import create_auth_app

        ctx = create_auth_app().state.ctx
    else:
        ctx = create_app().state.ctx
    if args.reconcile_owner:
        if not args.reconcile_owner.startswith(("user:", "club:")):
            parser.error("--reconcile-owner must be user:UUID or club:ID")
        print(json.dumps(ctx.upload_quotas.reconcile(args.reconcile_owner, args.quota_scope)))
        return 0
    storage = ctx.services.get("receipt_storage")
    if storage is None:
        raise RuntimeError("receipt maintenance requires the clubs plug-in")
    migrated = storage.migrate() if args.migrate else []
    results = storage.archive(apply=args.apply or (args.scheduled and ctx.settings.receipt_archive_enabled))
    print(json.dumps({"migrated": migrated, "receipts": results}))
    return 1 if any(result["state"] in {"error", "requires_migration"} for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())