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
        rows = [list(r) for r in markup.inline_keyboard]
        url = site_url()
        kb_url = os.getenv("KB_URL", "").strip()
        if kb_url.startswith("https://"):
            rows.append([InlineKeyboardButton(text="📚 База знаний", url=kb_url)])
        if url.startswith("https://"):
            rows.append([InlineKeyboardButton(text="🌐 Наш сайт", url=url)])
        channel = os.getenv("CHANNEL_URL", "https://t.me/paygood_oficial").strip()
        if channel.startswith("https://"):
            rows.append([InlineKeyboardButton(text="📢 Наш канал", url=channel)])
        if len(rows) == len(markup.inline_keyboard):
            return markup
        return InlineKeyboardMarkup(inline_keyboard=rows)

    kb.main_menu = main_menu
