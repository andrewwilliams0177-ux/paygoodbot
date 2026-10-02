from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Купить карту", callback_data="menu:plans")
    kb.button(text="🗂 Мои карты", callback_data="menu:cards")
    kb.button(text="💰 Баланс", callback_data="menu:balance")
    kb.button(text="➕ Пополнить", callback_data="menu:topup")
    kb.adjust(1)
    return kb.as_markup()


def plans_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for plan in config.CARD_PLANS:
        kb.button(
            text=f"{plan['title']} — {plan['price']} {plan['currency']}",
            callback_data=f"buy:{plan['id']}",
        )
    kb.button(text="⬅️ Назад", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def topup_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for amount in (300, 600, 1000, 2000):
        kb.button(text=f"{amount} ₽", callback_data=f"topup:{amount}")
    kb.button(text="⬅️ Назад", callback_data="menu:main")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def back_to_main() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    return kb.as_markup()
