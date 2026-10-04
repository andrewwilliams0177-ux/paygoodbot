"""Кнопка «Наш сайт» в главном меню бота. Адрес берётся из переменной Railway SITE_URL."""
import os


def site_url() -> str:
    return os.getenv("SITE_URL", "").strip()


def patch_menu():
    """Добавляет кнопку-ссылку в главное меню. Вызывать до импорта handlers.main."""
    import handlers.keyboards as kb
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    original = kb.main_menu

    def main_menu(*args, **kwargs):
        markup = original(*args, **kwargs)
        url = site_url()
        if not url.startswith("https://"):
            return markup
        rows = [list(r) for r in markup.inline_keyboard]
        rows.append([InlineKeyboardButton(text="🌐 Наш сайт", url=url)])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    kb.main_menu = main_menu
