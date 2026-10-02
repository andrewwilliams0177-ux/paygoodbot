from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery

import config
from db import database as db
from services.card_provider import get_card_provider
from services.payment_provider import get_payment_provider
from handlers.keyboards import main_menu, plans_menu, topup_menu, back_to_main

router = Router()


def plan_by_id(plan_id: str):
    return next((p for p in config.CARD_PLANS if p["id"] == plan_id), None)


@router.message(CommandStart())
async def cmd_start(message: Message):
    db.get_or_create_user(message.from_user.id, message.from_user.username)
    await message.answer(
        f"🚀 {config.BOT_NAME}\n\n"
        "Виртуальные карты и пополнение баланса в один клик.\n"
        "Выберите действие:",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "menu:main")
async def cb_main(call: CallbackQuery):
    await call.message.edit_text(f"🚀 {config.BOT_NAME} — главное меню:", reply_markup=main_menu())
    await call.answer()


@router.callback_query(F.data == "menu:plans")
async def cb_plans(call: CallbackQuery):
    await call.message.edit_text("Выберите тариф карты:", reply_markup=plans_menu())
    await call.answer()


@router.callback_query(F.data == "menu:balance")
async def cb_balance(call: CallbackQuery):
    balance = db.get_balance(call.from_user.id)
    await call.message.edit_text(
        f"💰 Ваш баланс: {balance} ₽",
        reply_markup=back_to_main(),
    )
    await call.answer()


@router.callback_query(F.data == "menu:topup")
async def cb_topup(call: CallbackQuery):
    await call.message.edit_text("Выберите сумму пополнения:", reply_markup=topup_menu())
    await call.answer()


@router.callback_query(F.data.startswith("topup:"))
async def cb_topup_amount(call: CallbackQuery):
    amount = int(call.data.split(":")[1])
    provider = get_payment_provider()
    payment = provider.create_payment(call.from_user.id, amount)

    # In mock mode this resolves instantly. With a real provider you'd
    # send the user provider["pay_url"] and confirm later via webhook.
    status = provider.check_payment(payment["payment_id"])
    if status == "paid":
        db.add_balance(call.from_user.id, amount)
        db.log_transaction(payment["payment_id"], call.from_user.id, amount, "topup", "paid")
        new_balance = db.get_balance(call.from_user.id)
        await call.message.edit_text(
            f"✅ Баланс пополнен на {amount} ₽\nТекущий баланс: {new_balance} ₽",
            reply_markup=back_to_main(),
        )
    else:
        await call.message.edit_text(
            f"⏳ Платёж создан, ожидаем подтверждения…\nID: {payment['payment_id']}",
            reply_markup=back_to_main(),
        )
    await call.answer()


@router.callback_query(F.data == "menu:cards")
async def cb_cards(call: CallbackQuery):
    cards = db.get_user_cards(call.from_user.id)
    if not cards:
        await call.message.edit_text("У вас пока нет карт.", reply_markup=back_to_main())
        await call.answer()
        return

    lines = ["🗂 Ваши карты:\n"]
    for c in cards:
        masked = c["pan"][:4] + " •••• •••• " + c["pan"][-4:]
        lines.append(f"• {masked}  |  {c['exp']}  |  статус: {c['status']}")
    await call.message.edit_text("\n".join(lines), reply_markup=back_to_main())
    await call.answer()


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(call: CallbackQuery):
    plan_id = call.data.split(":")[1]
    plan = plan_by_id(plan_id)
    if not plan:
        await call.answer("Тариф не найден", show_alert=True)
        return

    user_id = call.from_user.id
    if not db.deduct_balance(user_id, plan["price"]):
        balance = db.get_balance(user_id)
        await call.message.edit_text(
            f"❌ Недостаточно средств.\n"
            f"Нужно: {plan['price']} {plan['currency']}\n"
            f"У вас: {balance} {plan['currency']}",
            reply_markup=topup_menu(),
        )
        await call.answer()
        return

    provider = get_card_provider()
    card = provider.issue_card(user_id, plan_id)
    card["user_id"] = user_id
    card["plan_id"] = plan_id
    db.save_card(card)
    db.log_transaction(card["card_id"], user_id, plan["price"], "buy_card", "paid")

    await call.message.edit_text(
        f"✅ Карта «{plan['title']}» выпущена!\n\n"
        f"Номер: {card['pan']}\n"
        f"Срок: {card['exp']}\n"
        f"CVV: {card['cvv']}\n\n"
        f"Сохраните эти данные — здесь они показываются только один раз.",
        reply_markup=back_to_main(),
    )
    await call.answer()
