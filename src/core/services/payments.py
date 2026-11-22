"""Stripe-hosted Checkout; card and billing details never pass through the portal."""
from __future__ import annotations

import logging
import json
from typing import Any, Protocol
from urllib.parse import urlsplit

from fastapi import HTTPException

log = logging.getLogger("core.payments")


class PaymentGateway(Protocol):
    def checkout(self, order: dict[str, Any], return_url: str) -> dict[str, Any]: ...
    def event(self, payload: bytes, signature: str) -> dict[str, Any]: ...


class StripeGateway:
    def __init__(self, secret_key: str, webhook_secret: str):
        import stripe

        self.stripe = stripe
        self.secret_key, self.webhook_secret = secret_key, webhook_secret
        self.client = stripe.StripeClient(secret_key, max_network_retries=2)

    def checkout(self, order: dict[str, Any], return_url: str) -> dict[str, Any]:
        try:
            session = self.client.v1.checkout.sessions.create(
                {
                    "mode": "payment",
                    "payment_method_types": ["card"],
                    "client_reference_id": order["id"],
                    "metadata": {"order_id": order["id"]},
                    "line_items": [{
                        "price_data": {
                            "currency": order["currency"], "unit_amount": order["amount_cents"],
                            "product_data": {"name": "UTSU order " + order["id"]},
                        },
                        "quantity": 1,
                    }],
                    "success_url": return_url,
                    "cancel_url": return_url,
                    "expires_at": order["checkout_expires"],
                },
                options={"idempotency_key": "utsu-order:" + order["id"]},
            )
        except self.stripe.StripeError as exc:
            log.warning("Stripe checkout failed for order %s (%s)", order["id"], type(exc).__name__)
            raise HTTPException(502, "payment provider unavailable; retry this same order") from exc
        parsed = urlsplit(session.url or "")
        if parsed.scheme != "https" or parsed.hostname != "checkout.stripe.com":
            raise HTTPException(502, "payment provider returned an invalid checkout URL")
        return {"session_id": session.id, "checkout_url": session.url}

    def event(self, payload: bytes, signature: str) -> dict[str, Any]:
        try:
            if not isinstance(json.loads(payload), dict):
                log.warning("Rejected non-object Stripe webhook payload")
                raise HTTPException(400, "invalid payment webhook payload")
            return self.stripe.Webhook.construct_event(
                payload, signature, self.webhook_secret, tolerance=300,
            ).to_dict()
        except (ValueError, self.stripe.SignatureVerificationError) as exc:
            log.warning("Rejected invalid Stripe webhook (%s)", type(exc).__name__)
            raise HTTPException(400, "invalid payment webhook signature or payload") from exc
