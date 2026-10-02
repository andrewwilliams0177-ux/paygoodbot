import os

# --- Core ---
BOT_NAME = "PayGoodBot"
BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE") 

# --- Card issuing provider ---
# Which backend to use for issuing virtual cards.
# "mock"   -> fake cards, works with zero setup, good for testing the whole bot
# "stripe" -> Stripe Issuing (fill in STRIPE_SECRET_KEY below when ready)
# "lithic" -> Lithic (fill in LITHIC_API_KEY below when ready)
CARD_PROVIDER = os.getenv("CARD_PROVIDER", "mock")

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
LITHIC_API_KEY = os.getenv("LITHIC_API_KEY", "")

# --- Payment provider (topping up user balance) ---
# "mock" -> instantly "confirms" any payment, good for testing
PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "mock")

# --- Card plans shown to the user ---
CARD_PLANS = [
    {"id": "basic", "title": "Basic Virtual Card", "price": 300, "currency": "RUB"},
    {"id": "plus", "title": "Plus Virtual Card", "price": 600, "currency": "RUB"},
    {"id": "pro", "title": "Pro Virtual Card", "price": 1200, "currency": "RUB"},
]

DB_PATH = os.getenv("DB_PATH", "bot.db")
