"""Админ-часть: кнопки «Выдать/Отклонить», приём данных карты, команда /id."""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

import config
import orders
from db import database as db
from handlers.keyboards import back_to_main

router = Router()


def _name(u):
    return f"@{u.username}" if u.username else (u.full_name or str(u.id))


@router.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(f"Ваш ID: {message.from_user.id}\nID этого чата: {message.chat.id}")


# ── клиент покупает карту прямо в чате бота: тоже через заявку ──
@router.callback_query(F.data.startswith("buy:"), F.func(lambda c: orders.manual_mode()))
async def chat_buy(call: CallbackQuery):
    plan = next((p for p in config.CARD_PLANS if p["id"] == call.data.split(":")[1]), None)
    if not plan:
        await call.answer("Тариф не найден", show_alert=True)
        return
    u = call.from_user
    db.get_or_create_user(u.id, u.username)
    oid, err = await orders.place_order(u.id, u.full_name, u.username, plan)
    if err:
        await call.message.edit_text(f"❌ {err}", reply_markup=back_to_main())
    else:
        await call.message.edit_text(
            f"⏳ Заявка #{oid} на карту «{plan['title']}» принята.\n"
            "Как только карта будет готова, она придёт сюда и появится в приложении.",
            reply_markup=back_to_main(),
        )
    await call.answer()


def _guard(call: CallbackQuery):
    return call.message and call.message.chat.id == orders.admin_chat_id() and orders.is_admin(call.from_user.id)


@router.callback_query(F.data.startswith("ord_take:"))
async def take(call: CallbackQuery):
    if not _guard(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    oid = int(call.data.split(":")[1])
    ok, o = orders.take_order(oid, call.from_user.id, _name(call.from_user))
    if not ok:
        who = o["admin_name"] if o and o["admin_name"] else ""
        await call.answer(f"Уже обработано {who}".strip(), show_alert=True)
        return
    await call.message.edit_text(
        orders.request_text(o, f"Выдаёт: {_name(call.from_user)}\n\n"
                               "Ответьте (Reply) на это сообщение данными карты:\n"
                               "номер, срок, CVV — например:\n4111 1111 1111 1111 12/28 123"),
        reply_markup=orders.request_keyboard(oid, taken=True),
    )
    await call.answer("Заявка ваша. Ответьте на сообщение данными карты")


@router.callback_query(F.data.startswith("ord_rej:"))
async def reject(call: CallbackQuery):
    if not _guard(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    oid = int(call.data.split(":")[1])
    ok, o = orders.reject_order(oid, call.from_user.id)
    if not ok:
        await call.answer("Нельзя отклонить: заявка уже закрыта или в работе у другого", show_alert=True)
        return
    await call.message.edit_text(orders.request_text(o, f"❌ Отклонено: {_name(call.from_user)} (деньги возвращены)"))
    try:
        await call.bot.send_message(
            o["user_id"], f"❌ Заявка #{oid} на карту «{o['plan_title']}» отклонена.\n"
                          f"{o['price']} ₽ возвращены на ваш баланс.")
    except Exception:
        pass
    await call.answer("Отклонено, деньги возвращены")


@router.message(F.reply_to_message, F.text, F.func(lambda m: m.chat.id == orders.admin_chat_id()))
async def card_data(message: Message):
    o = orders.get_order_by_msg(message.reply_to_message.message_id)
    if not o or o["status"] != "processing":
        return
    if o["admin_id"] != message.from_user.id:
        await message.reply(f"Эту заявку выдаёт {o['admin_name']}.")
        return
    card = orders.parse_card(message.text)
    if not card:
        await message.reply("Не разобрал данные. Пришлите: номер, срок, CVV.\nПример: 4111 1111 1111 1111 12/28 123")
        return
    ok, o = orders.issue_order(o["order_id"], message.from_user.id, card)
    if not ok:
        await message.reply("Заявка уже закрыта.")
        return
    try:
        await message.bot.send_message(
            o["user_id"],
            f"✅ Ваша карта «{o['plan_title']}» готова!\n\n"
            f"Номер: {card['pan']}\nСрок: {card['exp']}\nCVV: {card['cvv']}\n\n"
            "Карта также появилась в приложении: Мои карты.")
        sent = "и отправлена клиенту"
    except Exception:
        sent = "но клиенту написать не удалось (он не запускал бота?) — карта есть в его приложении"
    try:
        await message.bot.edit_message_text(
            chat_id=message.chat.id, message_id=o["admin_msg_id"],
            text=orders.request_text(o, f"✅ Выдано: {o['admin_name']}"))
    except Exception:
        pass
    try:
        await message.delete()  # убираем данные карты из группы
    except Exception:
        pass
    await message.answer(f"✅ Карта по заявке #{o['order_id']} сохранена {sent}.")
