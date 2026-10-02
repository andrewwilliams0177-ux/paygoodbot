"""
Payment backend — handles topping up a user's in-bot balance
(e.g. via SBP / bank card acquiring).

Same idea as card_provider.py: one interface, swap the implementation
later without touching bot handlers.
"""

import uuid
from abc import ABC, abstractmethod

import config


class PaymentProvider(ABC):
    @abstractmethod
    def create_payment(self, user_id: int, amount: int) -> dict:
        """Start a payment. Return {"payment_id", "pay_url"} (pay_url can be None for mock)."""
        raise NotImplementedError

    @abstractmethod
    def check_payment(self, payment_id: str) -> str:
        """Return 'paid', 'pending', or 'failed'."""
        raise NotImplementedError


class MockPaymentProvider(PaymentProvider):
    """
    Instantly "confirms" any payment. Lets you test top-up -> balance ->
    buy-card flow end to end with zero real payment integration.
    """

    def create_payment(self, user_id: int, amount: int) -> dict:
        return {"payment_id": str(uuid.uuid4()), "pay_url": None}

    def check_payment(self, payment_id: str) -> str:
        return "paid"


class SBPProvider(PaymentProvider):
    """
    Stub for a real SBP / card-acquiring provider (e.g. YooKassa, CloudPayments,
    Tinkoff Acquiring). Fill in once you've picked one and have API keys.
    """

    def __init__(self, api_key: str):
        if not api_key:
            raise RuntimeError("Acquiring API key is not set")
        self.api_key = api_key

    def create_payment(self, user_id: int, amount: int) -> dict:
        raise NotImplementedError("Wire this up to your acquiring provider's create-payment endpoint.")

    def check_payment(self, payment_id: str) -> str:
        raise NotImplementedError("Wire this up to your acquiring provider's payment-status endpoint.")


def get_payment_provider() -> PaymentProvider:
    if config.PAYMENT_PROVIDER == "mock":
        return MockPaymentProvider()
    if config.PAYMENT_PROVIDER == "sbp":
        return SBPProvider(getattr(config, "SBP_API_KEY", ""))
    raise ValueError(f"Unknown PAYMENT_PROVIDER: {config.PAYMENT_PROVIDER}")
