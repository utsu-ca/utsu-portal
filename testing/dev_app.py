"""Entrypoint used only by the local Docker development stack."""

from fastapi import FastAPI

from utsu_core import create_app
from utsu_core.devadmin import check_dev_settings
from utsu_core.mailer import SMTPMailer
from utsu_core.settings import Settings


class DevSinkMailer(SMTPMailer):
    # Treat only the Compose mail catcher as local; the production mailer still requires STARTTLS.
    LOOPBACK = SMTPMailer.LOOPBACK | {"mail-sink"}


def create_dev_app() -> FastAPI:
    settings = Settings.from_env()
    if not settings.dev_admin:
        raise RuntimeError("The Docker dev entrypoint requires PORTAL_DEV_ADMIN=1")
    check_dev_settings(settings)
    mailer = DevSinkMailer("smtp://mail-sink:1025", "portal@dev-local.test")
    return create_app(settings, mailer=mailer)
