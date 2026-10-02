"""
Card issuing backend.

This is the ONE place you touch when you're ready to plug in a real
virtual-card API (Stripe Issuing, Lithic, Marqeta, etc).

Every provider below implements the same interface:
    issue_card(user_id, plan_id) -> dict with card_id, pan, exp, cvv

The rest of the bot never talks to a card API directly — it only calls
get_card_provider() and uses whatever it returns. That means you can
switch CARD_PROVIDER in config.py (or the env var) and nothing else in
the bot has to change.
"""

import random
import string
import uuid
from abc import ABC, abstractmethod

import config


class CardProvider(ABC):
    @abstractmethod
    def issue_card(self, user_id: int, plan_id: str) -> dict:
        """Return {"card_id", "pan", "exp", "cvv"} for a freshly issued card."""
        raise NotImplementedError


class MockCardProvider(CardProvider):
    """
    Fake card issuer. Generates a plausible-looking (but non-functional)
    card so you can test the entire bot flow — plans, payment, delivery,
    card list — before any real provider is connected.
    """

    def issue_card(self, user_id: int, plan_id: str) -> dict:
        pan = "4" + "".join(random.choices(string.digits, k=15))  # looks like a Visa PAN
        exp = f"{random.randint(1, 12):02d}/{random.randint(27, 30)}"
        cvv = "".join(random.choices(string.digits, k=3))
        return {
            "card_id": str(uuid.uuid4()),
            "pan": pan,
            "exp": exp,
            "cvv": cvv,
        }


class StripeIssuingProvider(CardProvider):
    """
    Stub for Stripe Issuing. Fill this in once you have a Stripe account
    with Issuing enabled and a cardholder set up.
    Docs: https://docs.stripe.com/issuing
    """

    def __init__(self, api_key: str):
        if not api_key:
            raise RuntimeError("STRIPE_SECRET_KEY is not set in config.py / env")
        self.api_key = api_key
        # import stripe
        # stripe.api_key = self.api_key

    def issue_card(self, user_id: int, plan_id: str) -> dict:
        raise NotImplementedError(
            "Wire this up to stripe.issuing.Card.create(...) once your "
            "Stripe Issuing account and cardholder are ready."
        )


class LithicProvider(CardProvider):
    """
    Stub for Lithic. Fill this in once you have a Lithic account and an
    approved card program.
    Docs: https://docs.lithic.com/docs/cards
    """

    def __init__(self, api_key: str):
        if not api_key:
            raise RuntimeError("LITHIC_API_KEY is not set in config.py / env")
        self.api_key = api_key

    def issue_card(self, user_id: int, plan_id: str) -> dict:
        raise NotImplementedError(
            "Wire this up to Lithic's POST /v1/cards endpoint once your "
            "account and card program are approved."
        )


def get_card_provider() -> CardProvider:
    if config.CARD_PROVIDER == "mock":
        return MockCardProvider()
    if config.CARD_PROVIDER == "stripe":
        return StripeIssuingProvider(config.STRIPE_SECRET_KEY)
    if config.CARD_PROVIDER == "lithic":
        return LithicProvider(config.LITHIC_API_KEY)
    raise ValueError(f"Unknown CARD_PROVIDER: {config.CARD_PROVIDER}")
