import os
import json
import logging
import asyncio
import time
import random
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple

import redis.asyncio as redis
from aiohttp import web

from aiogram import Bot, Dispatcher, F, Router, html
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

MSK_TZ = timezone(timedelta(hours=3))
RATE_LIMIT_SECONDS = 60

DEMO_MODE = True
DEMO_SUBSCRIBERS_KEY = "demo:subscribers"

MENU_REDIS_KEY = "menu:items"  # hash: {drink_name: price}

# ---------- Smart return ----------
CUSTOMERS_SET_KEY = "customers:set"
CUSTOMER_KEY_PREFIX = "customer:"             # hash customer:{user_id}
CUSTOMER_DRINKS_PREFIX = "customer:drinks:"   # hash customer:drinks:{user_id}

DEFAULT_RETURN_CYCLE_DAYS = 7
RETURN_COOLDOWN_DAYS = 30
RETURN_CHECK_EVERY_SECONDS = 6 * 60 * 60
RETURN_SEND_FROM_HOUR = 10
RETURN_SEND_TO_HOUR = 20
RETURN_DISCOUNT_PERCENT = 10


def get_moscow_time() -> datetime:
    return datetime.now(MSK_TZ)


def _parse_work_hours(obj: Any) -> Optional[Tuple[int, int]]:
    try:
        if isinstance(obj, list) and len(obj) == 2:
            start = int(obj[0])
            end = int(obj[1])
            if 0 <= start <= 23 and 0 <= end <= 23 and start != end:
                return start, end
    except Exception:
        return None
    return None


def load_config() -> Dict[str, Any]:
    default_config = {
        "name": "Кофейня «Уют» ☕",
        "phone": "+7 989 273-67-56",
        "admin_chat_id": 1471275603,
        "work_start": 9,
        "work_end": 21,
        "address": "г. Краснодар, ул. Красная, 123",
        "menu": {
            "☕ Капучино": 250,
            "🥛 Латте": 270,
            "🍵 Чай": 180,
            "⚡ Эспрессо": 200,
        },
        "return_cycle_days": DEFAULT_RETURN_CYCLE_DAYS,
    }

    try:
        with open("config.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            cafe = data.get("cafe", {})

            default_config.update(
                {
                    "name": cafe.get("name", default_config["name"]),
                    "phone": cafe.get("phone", default_config["phone"]),
                    "admin_chat_id": cafe.get("admin_chat_id", default_config["admin_chat_id"]),
                    "address": cafe.get("address", default_config["address"]),
                    "menu": cafe.get("menu", default_config["menu"]),
                    "return_cycle_days": int(cafe.get("return_cycle_days", default_config["return_cycle_days"])),
                }
            )

            wh = _parse_work_hours(cafe.get("work_hours"))
            if wh:
                default_config["work_start"], default_config["work_end"] = wh
            else:
                try:
                    ws = cafe.get("work_start", default_config["work_start"])
                    we = cafe.get("work_end", default_config["work_end"])
                    ws_i, we_i = int(ws), int(we)
                    if 0 <= ws_i <= 23 and 0 <= we_i <= 23 and ws_i != we_i:
                        default_config["work_start"] = ws_i
                        default_config["work_end"] = we_i
                except Exception:
                    pass
    except Exception:
        pass

    try:
        if int(default_config["return_cycle_days"]) <= 0:
            default_config["return_cycle_days"] = DEFAULT_RETURN_CYCLE_DAYS
    except Exception:
        default_config["return_cycle_days"] = DEFAULT_RETURN_CYCLE_DAYS

    return default_config


cafe_config = load_config()

CAFE_NAME = cafe_config["name"]
CAFE_PHONE = cafe_config["phone"]
ADMIN_ID = int(cafe_config["admin_chat_id"])
CAFE_ADDRESS = cafe_config.get("address", "")

MENU: Dict[str, int] = dict(cafe_config["menu"])
WORK_START = int(cafe_config["work_start"])
WORK_END = int(cafe_config["work_end"])

RETURN_CYCLE_DAYS = int(cafe_config.get("return_cycle_days", DEFAULT_RETURN_CYCLE_DAYS))

BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "cafebot123")
HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME", "chatbotify-2tjd.onrender.com")
PORT = int(os.getenv("PORT", 10000))

WEBHOOK_PATH = f"/{WEBHOOK_SECRET}/webhook"
WEBHOOK_URL = f"https://{HOSTNAME}{WEBHOOK_PATH}"

router = Router()


# ---------- States ----------
class OrderStates(StatesGroup):
    waiting_for_quantity = State()
    cart_view = State()
    cart_edit_pick_item = State()
    cart_edit_pick_action = State()
    waiting_for_confirmation = State()
    waiting_for_ready_time = State()


class BookingStates(StatesGroup):
    waiting_for_datetime = State()
    waiting_for_people = State()
    waiting_for_comment = State()


class MenuEditStates(StatesGroup):
    waiting_for_action = State()
    waiting_for_add_name = State()
    waiting_for_add_price = State()
    waiting_for_edit_name = State()
    waiting_for_edit_price = State()
    waiting_for_remove_name = State()


# ---------- Redis helper ----------
async def get_redis_client():
    client = redis.from_url(REDIS_URL)
    try:
        await client.ping()
        return client
    except Exception:
        await client.aclose()
        raise


def _rate_limit_key(user_id: int) -> str:
    return f"rate_limit:{user_id}"


# ---------- Working hours ----------
def is_cafe_open() -> bool:
    return WORK_START <= get_moscow_time().hour < WORK_END


def get_work_status() -> str:
    msk_hour = get_moscow_time().hour
    if is_cafe_open():
        remaining = max(0, WORK_END - msk_hour)
        return f"🟢 <b>Открыто</b> (ещё {remaining} ч.)"
    return f"🔴 <b>Закрыто</b>\n🕐 Открываемся: {WORK_START}:00 (МСК)"


def _address_line() -> str:
    return f"\n📍 <b>Адрес:</b> {html.quote(CAFE_ADDRESS)}" if CAFE_ADDRESS else ""


# ---------- DEMO subscribers ----------
async def register_demo_subscriber(user_id: int):
    if not DEMO_MODE:
        return
    try:
        r = await get_redis_client()
        await r.sadd(DEMO_SUBSCRIBERS_KEY, user_id)
        await r.expire(DEMO_SUBSCRIBERS_KEY, 60 * 60 * 24 * 30)
        await r.aclose()
    except Exception:
        pass


async def get_demo_recipients(include_admin: bool = True) -> set[int]:
    recipients: set[int] = set()
    try:
        r = await get_redis_client()
        raw = await r.smembers(DEMO_SUBSCRIBERS_KEY)
        await r.aclose()
        for x in raw:
            try:
                recipients.add(int(x))
            except Exception:
                pass
    except Exception:
        pass
    if include_admin:
        recipients.add(ADMIN_ID)
    return recipients


async def send_to_demo_audience(bot: Bot, text: str, include_admin: bool = True):
    recipients = await get_demo_recipients(include_admin=include_admin)
    for chat_id in recipients:
        try:
            await bot.send_message(chat_id, text, disable_web_page_preview=True)
        except Exception:
            try:
                r = await get_redis_client()
                await r.srem(DEMO_SUBSCRIBERS_KEY, chat_id)
                await r.aclose()
            except Exception:
                pass


# ---------- Menu sync ----------
async def sync_menu_from_redis():
    global MENU
    try:
        r = await get_redis_client()
        data = await r.hgetall(MENU_REDIS_KEY)
        if data:
            new_menu: Dict[str, int] = {}
            for k, v in data.items():
                try:
                    drink = k.decode("utf-8") if isinstance(k, (bytes, bytearray)) else str(k)
                    price_str = v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else str(v)
                    new_menu[drink] = int(price_str)
                except Exception:
                    continue
            if new_menu:
                MENU = dict(new_menu)
        else:
            if MENU:
                await r.hset(MENU_REDIS_KEY, mapping={k: str(v) for k, v in MENU.items()})
        await r.aclose()
    except Exception as e:
        logger.error(f"❌ sync_menu_from_redis error: {e}")


async def menu_set_item(drink: str, price: int):
    global MENU
    MENU[drink] = price
    try:
        r = await get_redis_client()
        await r.hset(MENU_REDIS_KEY, drink, str(price))
        await r.aclose()
    except Exception:
        pass


async def menu_delete_item(drink: str):
    global MENU
    MENU.pop(drink, None)
    try:
        r = await get_redis_client()
        await r.hdel(MENU_REDIS_KEY, drink)
        await r.aclose()
    except Exception:
        pass


# ---------- Smart return (minimal) ----------
async def customer_mark_order(*, user_id: int, first_name: str, username: str, cart: Dict[str, int], total_sum: int):
    now_ts = int(time.time())
    customer_key = f"{CUSTOMER_KEY_PREFIX}{user_id}"
    drinks_key = f"{CUSTOMER_DRINKS_PREFIX}{user_id}"
    last_drink = next(iter(cart.keys()), "")

    try:
        r = await get_redis_client()
        pipe = r.pipeline()

        pipe.sadd(CUSTOMERS_SET_KEY, user_id)
        pipe.hsetnx(customer_key, "first_order_ts", now_ts)
        pipe.hsetnx(customer_key, "offers_opt_out", 0)
        pipe.hsetnx(customer_key, "last_trigger_ts", 0)

        pipe.hset(customer_key, mapping={
            "first_name": first_name or "",
            "username": username or "",
            "last_order_ts": now_ts,
            "last_order_sum": int(total_sum),
            "last_drink": last_drink,
        })
        pipe.hincrby(customer_key, "total_orders", 1)
        pipe.hincrby(customer_key, "total_spent", int(total_sum))

        for drink, qty in cart.items():
            pipe.hincrby(drinks_key, drink, int(qty))

        await pipe.execute()
        await r.aclose()
    except Exception:
        pass


async def customer_set_offers_opt(user_id: int, opt_out: bool):
    customer_key = f"{CUSTOMER_KEY_PREFIX}{user_id}"
    try:
        r = await get_redis_client()
        await r.hset(customer_key, "offers_opt_out", 1 if opt_out else 0)
        await r.sadd(CUSTOMERS_SET_KEY, user_id)
        await r.aclose()
    except Exception:
        pass


async def _get_favorite_drink(user_id: int) -> str:
    drinks_key = f"{CUSTOMER_DRINKS_PREFIX}{user_id}"
    try:
        r = await get_redis_client()
        data = await r.hgetall(drinks_key)
        await r.aclose()
        if not data:
            return ""
        best_name = ""
        best_cnt = -1
        for k, v in data.items():
            try:
                name = k.decode("utf-8") if isinstance(k, (bytes, bytearray)) else str(k)
                cnt = int(v.decode("utf-8")) if isinstance(v, (bytes, bytearray)) else int(v)
                if cnt > best_cnt:
                    best_cnt = cnt
                    best_name = name
            except Exception:
                continue
        return best_name
    except Exception:
        return ""


def _in_send_window_msk() -> bool:
    h = get_moscow_time().hour
    return RETURN_SEND_FROM_HOUR <= h < RETURN_SEND_TO_HOUR


def _promo_code(user_id: int) -> str:
    return f"CB{user_id % 10000:04d}{int(time.time()) % 10000:04d}"


async def smart_return_check_and_send(bot: Bot):
    if not _in_send_window_msk():
        return

    now_ts = int(time.time())

    try:
        r = await get_redis_client()
        ids = await r.smembers(CUSTOMERS_SET_KEY)
        await r.aclose()
    except Exception:
        ids = []

    for raw_id in ids:
        try:
            user_id = int(raw_id)
        except Exception:
            continue

        customer_key = f"{CUSTOMER_KEY_PREFIX}{user_id}"
        try:
            r = await get_redis_client()
            profile = await r.hgetall(customer_key)
            await r.aclose()
        except Exception:
            profile = {}

        if not profile:
            continue

        def _get(field: str) -> str:
            v = profile.get(field.encode("utf-8"), profile.get(field))
            if v is None:
                return ""
            if isinstance(v, (bytes, bytearray)):
                return v.decode("utf-8", errors="ignore")
            return str(v)

        if _get("offers_opt_out") == "1":
            continue

        last_order_ts_str = _get("last_order_ts")
        if not last_order_ts_str:
            continue
        try:
            last_order_ts = int(float(last_order_ts_str))
        except Exception:
            continue

        days_since = (now_ts - last_order_ts) // 86400
        if days_since < RETURN_CYCLE_DAYS:
            continue

        last_trigger_ts_str = _get("last_trigger_ts")
        try:
            last_trigger_ts = int(float(last_trigger_ts_str)) if last_trigger_ts_str else 0
        except Exception:
            last_trigger_ts = 0

        if last_trigger_ts and (now_ts - last_trigger_ts) < (RETURN_COOLDOWN_DAYS * 86400):
            continue

        first_name = _get("first_name") or "друг"
        favorite = await _get_favorite_drink(user_id)
        if not favorite:
            favorite = _get("last_drink") or "напиток"

        promo = _promo_code(user_id)
        text = (
            f"{html.quote(first_name)}, давно не виделись ☕\n\n"
            f"Ваш любимый <b>{html.quote(favorite)}</b> сегодня со скидкой <b>{RETURN_DISCOUNT_PERCENT}%</b>.\n"
            f"Промокод: <code>{promo}</code>\n\n"
            "Сделаем заказ? Нажмите /start.\n\n"
            "Отключить: /offers_off"
        )

        try:
            await bot.send_message(user_id, text)
            try:
                r = await get_redis_client()
                await r.hset(customer_key, "last_trigger_ts", now_ts)
                await r.aclose()
            except Exception:
                pass
        except Exception:
            try:
                r = await get_redis_client()
                await r.srem(CUSTOMERS_SET_KEY, user_id)
                await r.aclose()
            except Exception:
                pass


async def smart_return_loop(bot: Bot):
    while True:
        try:
            await smart_return_check_and_send(bot)
        except Exception as e:
            logger.error(f"❌ smart_return_loop error: {e}")
        await asyncio.sleep(RETURN_CHECK_EVERY_SECONDS)


# ---------- Buttons ----------
BTN_CALL = "📞 Позвонить"
BTN_HOURS = "⏰ Часы работы"
BTN_STATS = "📊 Статистика"
BTN_BOOKING = "📅 Бронирование"
BTN_MENU_EDIT = "🛠 Меню"

BTN_CART = "🛒 Корзина"
BTN_CHECKOUT = "✅ Оформить"
BTN_CLEAR_CART = "🧹 Очистить"
BTN_CANCEL_ORDER = "❌ Отменить заказ"
BTN_EDIT_CART = "✏️ Изменить"

BTN_CANCEL = "🔙 Отмена"
BTN_BACK = "⬅️ Назад"

BTN_CONFIRM = "Подтвердить"
BTN_MENU = "Меню"

BTN_READY_NOW = "🚶 Сейчас"
BTN_READY_20 = "⏱ Через 20 мин"

CART_ACT_PLUS = "➕ +1"
CART_ACT_MINUS = "➖ -1"
CART_ACT_DEL = "🗑 Удалить"
CART_ACT_DONE = "✅ Готово"

MENU_EDIT_ADD = "➕ Добавить позицию"
MENU_EDIT_EDIT = "✏️ Изменить цену"
MENU_EDIT_DEL = "🗑 Удалить позицию"


# ---------- Keyboards ----------
def create_main_keyboard() -> ReplyKeyboardMarkup:
    kb: list[list[KeyboardButton]] = []
    for drink in MENU.keys():
        kb.append([KeyboardButton(text=drink)])
    kb.append([KeyboardButton(text=BTN_CART), KeyboardButton(text=BTN_CHECKOUT), KeyboardButton(text=BTN_BOOKING)])
    kb.append([KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_CALL), KeyboardButton(text=BTN_HOURS)])
    kb.append([KeyboardButton(text=BTN_MENU_EDIT)])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, is_persistent=True)  # [web:60]


def create_cart_keyboard(cart_has_items: bool) -> ReplyKeyboardMarkup:
    kb: list[list[KeyboardButton]] = []
    kb.append([KeyboardButton(text=BTN_CART), KeyboardButton(text=BTN_CHECKOUT)])
    if cart_has_items:
        kb.append([KeyboardButton(text=BTN_EDIT_CART), KeyboardButton(text=BTN_CLEAR_CART), KeyboardButton(text=BTN_CANCEL_ORDER)])
    else:
        kb.append([KeyboardButton(text=BTN_CANCEL_ORDER)])
    for drink in MENU.keys():
        kb.append([KeyboardButton(text=drink)])
    kb.append([KeyboardButton(text=BTN_BOOKING), KeyboardButton(text=BTN_STATS)])
    kb.append([KeyboardButton(text=BTN_CALL), KeyboardButton(text=BTN_HOURS), KeyboardButton(text=BTN_MENU_EDIT)])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, is_persistent=True)  # [web:60]


def create_quantity_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1️⃣"), KeyboardButton(text="2️⃣"), KeyboardButton(text="3️⃣")],
            [KeyboardButton(text="4️⃣"), KeyboardButton(text="5️⃣"), KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,  # [web:60]
    )


def create_confirm_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CONFIRM), KeyboardButton(text=BTN_CART)],
            [KeyboardButton(text=BTN_CANCEL_ORDER)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,  # [web:60]
    )


def create_ready_time_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_READY_NOW), KeyboardButton(text=BTN_READY_20)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,  # [web:60]
    )


def create_cart_pick_item_keyboard(cart: Dict[str, int]) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    for drink in cart.keys():
        rows.append([KeyboardButton(text=drink)])
    rows.append([KeyboardButton(text=BTN_CANCEL), KeyboardButton(text=BTN_CART)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)  # [web:60]


def create_cart_edit_actions_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=CART_ACT_PLUS), KeyboardButton(text=CART_ACT_MINUS)],
            [KeyboardButton(text=CART_ACT_DEL), KeyboardButton(text=CART_ACT_DONE)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,  # [web:60]
    )


def create_info_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CART), KeyboardButton(text=BTN_BOOKING), KeyboardButton(text=BTN_STATS)],
            [KeyboardButton(text=BTN_CALL), KeyboardButton(text=BTN_HOURS), KeyboardButton(text=BTN_MENU_EDIT)],
        ],
        resize_keyboard=True,
        is_persistent=True,  # [web:60]
    )


def create_booking_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL), KeyboardButton(text=BTN_MENU)]],
        resize_keyboard=True,
        one_time_keyboard=True,  # [web:60]
    )


def create_booking_people_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1"), KeyboardButton(text="2"), KeyboardButton(text="3"), KeyboardButton(text="4")],
            [KeyboardButton(text="5"), KeyboardButton(text="6"), KeyboardButton(text="7"), KeyboardButton(text="8")],
            [KeyboardButton(text="9"), KeyboardButton(text="10"), KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,  # [web:60]
    )


def create_menu_edit_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU_EDIT_ADD), KeyboardButton(text=MENU_EDIT_EDIT)],
            [KeyboardButton(text=MENU_EDIT_DEL), KeyboardButton(text=BTN_BACK)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,  # [web:60]
    )


def create_menu_edit_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_BACK)]], resize_keyboard=True, one_time_keyboard=True)  # [web:60]


# ---------- Text helpers ----------
WELCOME_VARIANTS = [
    "Рад тебя видеть, {name}! Сегодня что-то классическое или попробуем новинку?",
    "{name}, добро пожаловать! Я уже грею молоко — выбирай, что приготовить.",
    "Заходи, {name}! Сейчас самое время для вкусного перерыва.",
    "{name}, привет! Устроим небольшой кофейный ритуал?",
    "Отлично, что заглянул, {name}! Давай подберём идеальный напиток под настроение.",
]

CHOICE_VARIANTS = [
    "Отличный выбор! Такое сейчас особенно популярно.",
    "Классика, которая никогда не подводит.",
    "Мне тоже нравится этот вариант — не прогадаешь.",
    "Прекрасный вкус! Это один из хитов нашего меню.",
    "Звучит вкусно — уже представляю аромат.",
]

FINISH_VARIANTS = [
    "Спасибо за заказ, {name}! Буду рад увидеть тебя снова.",
    "Рад был помочь с выбором, {name}. Заглядывай ещё — всегда ждём.",
    "Заказ готовим с заботой. Возвращайся, когда захочется повторить.",
]


def get_user_name(message: Message) -> str:
    if message.from_user is None:
        return "друг"
    return message.from_user.first_name or "друг"


def get_closed_message() -> str:
    menu_text = " • ".join([f"<b>{html.quote(drink)}</b> {price}₽" for drink, price in MENU.items()])
    return (
        f"🔒 <b>{html.quote(CAFE_NAME)} сейчас закрыто!</b>\n\n"
        f"⏰ {get_work_status()}{_address_line()}\n\n"
        f"☕ <b>Наше меню:</b>\n{menu_text}\n\n"
        f"📞 <b>Связаться:</b>\n<code>{html.quote(CAFE_PHONE)}</code>"
    )


def _is_reserved_button(text: str) -> bool:
    reserved = {
        BTN_CALL, BTN_HOURS, BTN_STATS, BTN_BOOKING, BTN_MENU_EDIT,
        BTN_CART, BTN_CHECKOUT, BTN_CLEAR_CART, BTN_CANCEL_ORDER, BTN_EDIT_CART,
        BTN_CANCEL, BTN_BACK, BTN_CONFIRM, BTN_MENU,
        BTN_READY_NOW, BTN_READY_20,
        CART_ACT_PLUS, CART_ACT_MINUS, CART_ACT_DEL, CART_ACT_DONE,
        MENU_EDIT_ADD, MENU_EDIT_EDIT, MENU_EDIT_DEL,
    }
    return text in reserved


# ---------- Cart helpers ----------
def _get_cart(data: Dict[str, Any]) -> Dict[str, int]:
    cart = data.get("cart")
    if isinstance(cart, dict):
        out: Dict[str, int] = {}
        for k, v in cart.items():
            try:
                out[str(k)] = int(v)
            except Exception:
                continue
        return out
    return {}


def _cart_total(cart: Dict[str, int]) -> int:
    total = 0
    for drink, qty in cart.items():
        price = MENU.get(drink)
        if price is None:
            continue
        total += price * int(qty)
    return total


def _cart_lines(cart: Dict[str, int]) -> list[str]:
    lines: list[str] = []
    for drink, qty in cart.items():
        price = MENU.get(drink)
        if price is None:
            continue
        sub = price * int(qty)
        lines.append(f"• {html.quote(drink)} × {qty} = <b>{sub}₽</b>")
    return lines


def _cart_text(cart: Dict[str, int]) -> str:
    if not cart:
        return "🛒 <b>Корзина пустая</b>\n\nЧтобы добавить: нажмите напиток → выберите количество."
    return "🛒 <b>Ваш заказ:</b>\n" + "\n".join(_cart_lines(cart)) + f"\n\n💰 Итого: <b>{_cart_total(cart)}₽</b>"


async def _show_cart(message: Message, state: FSMContext):
    data = await state.get_data()
    cart = _get_cart(data)
    await state.set_state(OrderStates.cart_view)
    await state.update_data(cart=cart)
    await message.answer(_cart_text(cart), reply_markup=create_cart_keyboard(bool(cart)))


# ---------- Admin demo message ----------
def _format_ready_line(ready_in_min: int) -> str:
    if ready_in_min <= 0:
        return "⏱ Готовность: <b>как можно скорее</b>"
    ready_at = (get_moscow_time() + timedelta(minutes=ready_in_min)).strftime("%H:%M")
    return f"⏱ Готовность: <b>через {ready_in_min} мин</b> (к {ready_at} МСК)"


def build_admin_order_messages(*, order_num: str, user_id: int, user_name: str, cart: Dict[str, int], total: int, ready_in_min: int) -> tuple[str, str]:
    safe_user_name = html.quote(user_name)
    user_link = f'<a href="tg://user?id={user_id}">{safe_user_name}</a>'
    items_text = "\n".join(_cart_lines(cart)) if cart else "—"
    msg1 = (
        f"🔔 <b>НОВЫЙ ЗАКАЗ #{order_num}</b> | {html.quote(CAFE_NAME)}\n\n"
        f"{user_link}\n<code>{user_id}</code>\n\n"
        f"{items_text}\n\n"
        f"💰 Итого: <b>{total} ₽</b>\n"
        f"{_format_ready_line(ready_in_min)}"
    )
    msg2 = "ℹ️ <b>DEMO</b>: так будет выглядеть уведомление администратору."
    return msg1, msg2


# -------------------------
# /offers
# -------------------------
@router.message(Command("offers_off"))
async def offers_off(message: Message):
    await register_demo_subscriber(message.from_user.id)
    await customer_set_offers_opt(message.from_user.id, opt_out=True)
    await message.answer("Ок, персональные предложения отключены. /offers_on — включить.", reply_markup=create_main_keyboard())


@router.message(Command("offers_on"))
async def offers_on(message: Message):
    await register_demo_subscriber(message.from_user.id)
    await customer_set_offers_opt(message.from_user.id, opt_out=False)
    await message.answer("Готово! Персональные предложения включены.", reply_markup=create_main_keyboard())


# -------------------------
# /start
# -------------------------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await register_demo_subscriber(message.from_user.id)

    name = get_user_name(message)
    msk_time = get_moscow_time().strftime("%H:%M")
    welcome = random.choice(WELCOME_VARIANTS).format(name=name)

    if is_cafe_open():
        await message.answer(
            f"{welcome}\n\n"
            f"🕐 <i>Московское время: {msk_time}</i>\n"
            f"🏪 {get_work_status()}{_address_line()}\n\n"
            "Чтобы добавить в корзину: нажмите напиток → выберите количество.\n"
            "Корзина — кнопка «🛒 Корзина».",
            reply_markup=create_main_keyboard(),
        )
    else:
        await message.answer(get_closed_message(), reply_markup=create_info_keyboard())


# -------------------------
# Common buttons
# -------------------------
@router.message(F.text == BTN_CALL)
async def call_phone(message: Message):
    await register_demo_subscriber(message.from_user.id)
    await message.answer(
        f"📞 <b>Телефон {html.quote(CAFE_NAME)}:</b>\n<code>{html.quote(CAFE_PHONE)}</code>",
        reply_markup=create_main_keyboard() if is_cafe_open() else create_info_keyboard(),
    )


@router.message(F.text == BTN_HOURS)
async def show_hours(message: Message):
    await register_demo_subscriber(message.from_user.id)
    msk_time = get_moscow_time().strftime("%H:%M")
    await message.answer(
        f"🕐 <b>Сейчас:</b> {msk_time} (МСК)\n🏪 {get_work_status()}{_address_line()}",
        reply_markup=create_main_keyboard() if is_cafe_open() else create_info_keyboard(),
    )


@router.message(F.text == BTN_STATS)
async def stats_button(message: Message):
    await register_demo_subscriber(message.from_user.id)

    if message.from_user.id != ADMIN_ID:
        await message.answer("📊 <b>Статистика (DEMO)</b>\n\nВсего заказов: <b>123</b>", reply_markup=create_main_keyboard())
        return

    try:
        r_client = await get_redis_client()
        total_orders = int(await r_client.get("stats:total_orders") or 0)
        total_revenue = int(await r_client.get("stats:total_revenue") or 0)

        lines = []
        for drink, price in MENU.items():
            cnt = int(await r_client.get(f"stats:drink:{drink}") or 0)
            rev = int(await r_client.get(f"stats:drink_revenue:{drink}") or 0)
            lines.append(f"• {html.quote(drink)}: <b>{cnt}</b> шт., <b>{rev}₽</b>")

        await r_client.aclose()

        text = (
            "📊 <b>Статистика</b>\n\n"
            f"Всего заказов: <b>{total_orders}</b>\n"
            f"Выручка всего: <b>{total_revenue}₽</b>\n\n"
            "<b>По позициям:</b>\n" + "\n".join(lines)
        )
        await message.answer(text, reply_markup=create_main_keyboard())
    except Exception:
        await message.answer("❌ Ошибка статистики", reply_markup=create_main_keyboard())


# -------------------------
# Cart buttons
# -------------------------
@router.message(F.text == BTN_CART)
async def cart_button(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)
    if not is_cafe_open():
        await message.answer(get_closed_message(), reply_markup=create_info_keyboard())
        return
    await _show_cart(message, state)


@router.message(F.text == BTN_CLEAR_CART)
async def clear_cart(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)
    await state.update_data(cart={})
    await _show_cart(message, state)


@router.message(F.text == BTN_CANCEL_ORDER)
async def cancel_order(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)
    await state.clear()
    await message.answer("❌ Заказ отменён.", reply_markup=create_main_keyboard() if is_cafe_open() else create_info_keyboard())


# -------------------------
# Add item: drink -> quantity
# -------------------------
async def _start_add_item(message: Message, state: FSMContext, drink: str):
    price = MENU.get(drink)
    if price is None:
        await message.answer("Этого напитка уже нет в меню.", reply_markup=create_main_keyboard())
        return

    data = await state.get_data()
    cart = _get_cart(data)

    await state.set_state(OrderStates.waiting_for_quantity)
    await state.update_data(current_drink=drink, current_price=price, cart=cart)

    choice_text = random.choice(CHOICE_VARIANTS).format(name=get_user_name(message))
    await message.answer(
        f"{choice_text}\n\n🥤 <b>{html.quote(drink)}</b>\n💰 <b>{price} ₽</b>\n\nСколько порций добавить в корзину?",
        reply_markup=create_quantity_keyboard(),
    )


@router.message(StateFilter(OrderStates.waiting_for_quantity))
async def process_quantity(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)

    if message.text == BTN_CANCEL:
        data = await state.get_data()
        cart = _get_cart(data)
        await message.answer("Ок. Добавление отменено.", reply_markup=create_cart_keyboard(bool(cart)) if cart else create_main_keyboard())
        return

    try:
        quantity = int((message.text or "")[0])
        if not (1 <= quantity <= 5):
            raise ValueError
    except Exception:
        await message.answer("Нажмите количество 1–5.", reply_markup=create_quantity_keyboard())
        return

    data = await state.get_data()
    drink = str(data.get("current_drink") or "")
    cart = _get_cart(data)

    if not drink or drink not in MENU:
        await state.clear()
        await message.answer("Ошибка. Нажмите /start.", reply_markup=create_main_keyboard())
        return

    cart[drink] = int(cart.get(drink, 0)) + quantity
    await state.update_data(cart=cart)
    await state.set_state(OrderStates.cart_view)

    await message.answer(
        f"✅ Добавил в корзину: <b>{html.quote(drink)}</b> × {quantity}\n\n{_cart_text(cart)}",
        reply_markup=create_cart_keyboard(True),
    )


# -------------------------
# Cart edit
# -------------------------
@router.message(F.text == BTN_EDIT_CART)
async def edit_cart(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)
    data = await state.get_data()
    cart = _get_cart(data)
    if not cart:
        await message.answer("Корзина пустая.", reply_markup=create_main_keyboard())
        return
    await state.set_state(OrderStates.cart_edit_pick_item)
    await message.answer("Выберите позицию для изменения:", reply_markup=create_cart_pick_item_keyboard(cart))


@router.message(StateFilter(OrderStates.cart_edit_pick_item))
async def pick_item_to_edit(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)
    text = (message.text or "").strip()

    if text in {BTN_CANCEL, BTN_CART}:
        await _show_cart(message, state)
        return

    data = await state.get_data()
    cart = _get_cart(data)
    if text not in cart:
        await message.answer("Выберите позицию кнопкой.", reply_markup=create_cart_pick_item_keyboard(cart))
        return

    await state.set_state(OrderStates.cart_edit_pick_action)
    await state.update_data(edit_item=text)
    await message.answer(f"Что сделать с <b>{html.quote(text)}</b>?", reply_markup=create_cart_edit_actions_keyboard())


@router.message(StateFilter(OrderStates.cart_edit_pick_action))
async def cart_edit_action(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)
    text = (message.text or "").strip()

    if text == BTN_CANCEL:
        await _show_cart(message, state)
        return

    data = await state.get_data()
    cart = _get_cart(data)
    item = str(data.get("edit_item") or "")

    if not item or item not in cart:
        await _show_cart(message, state)
        return

    if text == CART_ACT_DONE:
        await _show_cart(message, state)
        return

    if text == CART_ACT_PLUS:
        cart[item] = int(cart.get(item, 0)) + 1
    elif text == CART_ACT_MINUS:
        cart[item] = int(cart.get(item, 0)) - 1
        if cart[item] <= 0:
            cart.pop(item, None)
    elif text == CART_ACT_DEL:
        cart.pop(item, None)
    else:
        await message.answer("Выберите действие кнопкой.", reply_markup=create_cart_edit_actions_keyboard())
        return

    await state.update_data(cart=cart)

    if not cart:
        await state.set_state(OrderStates.cart_view)
        await message.answer("Корзина теперь пустая.", reply_markup=create_main_keyboard())
        return

    await state.set_state(OrderStates.cart_edit_pick_item)
    await message.answer(_cart_text(cart), reply_markup=create_cart_pick_item_keyboard(cart))


# -------------------------
# Checkout -> confirm -> ready time -> finalize
# -------------------------
@router.message(F.text == BTN_CHECKOUT)
async def checkout(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)
    if not is_cafe_open():
        await message.answer(get_closed_message(), reply_markup=create_info_keyboard())
        return

    data = await state.get_data()
    cart = _get_cart(data)
    if not cart:
        await message.answer("Корзина пустая — добавьте позицию.", reply_markup=create_main_keyboard())
        return

    await state.set_state(OrderStates.waiting_for_confirmation)
    await message.answer("✅ <b>Подтвердите заказ</b>\n\n" + _cart_text(cart), reply_markup=create_confirm_keyboard())


@router.message(StateFilter(OrderStates.waiting_for_confirmation))
async def process_cart_confirmation(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)

    if message.text == BTN_CART:
        await _show_cart(message, state)
        return

    if message.text == BTN_CANCEL_ORDER:
        await state.clear()
        await message.answer("❌ Заказ отменён.", reply_markup=create_main_keyboard())
        return

    if message.text != BTN_CONFIRM:
        await message.answer("Нажмите «Подтвердить» или «Корзина».", reply_markup=create_confirm_keyboard())
        return

    await state.set_state(OrderStates.waiting_for_ready_time)
    await message.answer("⏱ <b>Когда удобно забрать заказ?</b>", reply_markup=create_ready_time_keyboard())


async def _finalize_order(message: Message, state: FSMContext, ready_in_min: int):
    user_id = message.from_user.id
    data = await state.get_data()
    cart = _get_cart(data)
    if not cart:
        await state.clear()
        await message.answer("Корзина пустая. Нажмите /start.", reply_markup=create_main_keyboard())
        return

    try:
        r_client = await get_redis_client()
        last_order = await r_client.get(_rate_limit_key(user_id))
        if last_order and time.time() - float(last_order) < RATE_LIMIT_SECONDS:
            await message.answer(f"⏳ Подождите {RATE_LIMIT_SECONDS} секунд между заказами.", reply_markup=create_main_keyboard())
            await r_client.aclose()
            await state.clear()
            return
        await r_client.setex(_rate_limit_key(user_id), RATE_LIMIT_SECONDS, time.time())
        await r_client.aclose()
    except Exception:
        pass

    total = _cart_total(cart)

    order_id = f"order:{int(time.time())}:{user_id}"
    order_num = order_id.split(":")[-1]

    user_name = message.from_user.username or message.from_user.first_name or "Клиент"
    ready_at_dt = get_moscow_time() + timedelta(minutes=max(0, ready_in_min))
    ready_at_str = ready_at_dt.strftime("%H:%M")

    # save order + stats
    try:
        r_client = await get_redis_client()
        await r_client.hset(
            order_id,
            mapping={
                "user_id": user_id,
                "username": user_name,
                "cart_json": json.dumps(cart, ensure_ascii=False),
                "total": total,
                "ready_in_min": ready_in_min,
                "ready_at_msk": ready_at_str,
                "timestamp": datetime.now().isoformat(),
            },
        )
        await r_client.expire(order_id, 86400)

        # ---- Stats counters ----
        await r_client.incr("stats:total_orders")
        await r_client.incrby("stats:total_revenue", int(total))  # [web:216]

        for drink, qty in cart.items():
            qty_i = int(qty)
            price = int(MENU.get(drink, 0))
            await r_client.incrby(f"stats:drink:{drink}", qty_i)  # [web:216]
            await r_client.incrby(f"stats:drink_revenue:{drink}", qty_i * price)  # [web:216]

        await r_client.aclose()
    except Exception:
        pass

    try:
        await customer_mark_order(
            user_id=user_id,
            first_name=message.from_user.first_name or "",
            username=message.from_user.username or "",
            cart=cart,
            total_sum=int(total),
        )
    except Exception:
        pass

    msg1, msg2 = build_admin_order_messages(
        order_num=order_num,
        user_id=user_id,
        user_name=user_name,
        cart=cart,
        total=total,
        ready_in_min=ready_in_min,
    )
    await send_to_demo_audience(message.bot, msg1, include_admin=True)
    await send_to_demo_audience(message.bot, msg2, include_admin=True)

    finish_text = random.choice(FINISH_VARIANTS).format(name=get_user_name(message))
    ready_user_line = "⏱ Готовность: как можно скорее" if ready_in_min <= 0 else f"⏱ Готовность: через {ready_in_min} мин (к {ready_at_str} МСК)"
    items = "\n".join(_cart_lines(cart))

    await message.answer(
        f"🎉 <b>Заказ #{order_num} принят!</b>\n\n{items}\n\n💰 Итого: <b>{total}₽</b>\n{ready_user_line}\n\n{finish_text}",
        reply_markup=create_main_keyboard(),
    )
    await state.clear()


@router.message(StateFilter(OrderStates.waiting_for_ready_time))
async def process_ready_time(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)

    if message.text == BTN_CANCEL:
        await _show_cart(message, state)
        return

    if message.text == BTN_READY_NOW:
        await _finalize_order(message, state, 0)
        return

    if message.text == BTN_READY_20:
        await _finalize_order(message, state, 20)
        return

    await message.answer("Выберите вариант кнопкой.", reply_markup=create_ready_time_keyboard())


# -------------------------
# Booking (компактно)
# -------------------------
@router.message(F.text == BTN_BOOKING)
async def booking_start(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)
    await state.clear()

    if not is_cafe_open():
        await message.answer(get_closed_message(), reply_markup=create_info_keyboard())
        return

    await state.set_state(BookingStates.waiting_for_datetime)
    await message.answer(
        "📅 <b>Бронирование</b>\n\nНапишите дату и время: <code>15.02 19:00</code>\nИли «Отмена».",
        reply_markup=create_booking_cancel_keyboard(),
    )


@router.message(StateFilter(BookingStates.waiting_for_datetime))
async def booking_datetime(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)

    if message.text in {BTN_CANCEL, BTN_MENU}:
        await state.clear()
        await message.answer("Ок, отменил.", reply_markup=create_main_keyboard())
        return

    m = re.match(r"^\s*(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})\s*$", message.text or "")
    if not m:
        await message.answer("Формат: <code>15.02 19:00</code>", reply_markup=create_booking_cancel_keyboard())
        return

    day, month, hour, minute = map(int, m.groups())
    year = get_moscow_time().year
    try:
        dt = datetime(year, month, day, hour, minute, tzinfo=MSK_TZ)
    except Exception:
        await message.answer("Дата/время некорректны.", reply_markup=create_booking_cancel_keyboard())
        return

    await state.update_data(booking_dt=dt.strftime("%d.%m %H:%M"))
    await state.set_state(BookingStates.waiting_for_people)
    await message.answer("Сколько гостей?", reply_markup=create_booking_people_keyboard())


@router.message(StateFilter(BookingStates.waiting_for_people))
async def booking_people(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)

    if message.text in {BTN_CANCEL, BTN_MENU}:
        await state.clear()
        await message.answer("Ок, отменил.", reply_markup=create_main_keyboard())
        return

    try:
        people = int((message.text or "").strip())
        if not (1 <= people <= 10):
            raise ValueError
    except Exception:
        await message.answer("Число 1–10.", reply_markup=create_booking_people_keyboard())
        return

    await state.update_data(booking_people=people)
    await state.set_state(BookingStates.waiting_for_comment)
    await message.answer("Комментарий (или <code>-</code>):", reply_markup=create_booking_cancel_keyboard())


@router.message(StateFilter(BookingStates.waiting_for_comment))
async def booking_finish(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)

    if message.text in {BTN_CANCEL, BTN_MENU}:
        await state.clear()
        await message.answer("Ок, отменил.", reply_markup=create_main_keyboard())
        return

    data = await state.get_data()
    dt_str = data.get("booking_dt", "—")
    people = int(data.get("booking_people", 0) or 0)
    comment = (message.text or "").strip() or "-"
    comment_out = "—" if comment == "-" else comment

    booking_id = f"{int(time.time())}{message.from_user.id}"
    await message.answer("✅ Заявка на бронь принята!", reply_markup=create_main_keyboard())

    user_name = message.from_user.username or message.from_user.first_name or "Клиент"
    msg1 = (
        f"📋 <b>НОВАЯ БРОНЬ #{booking_id}</b> | {html.quote(CAFE_NAME)}\n\n"
        f"<a href=\"tg://user?id={message.from_user.id}\">{html.quote(user_name)}</a>\n"
        f"<code>{message.from_user.id}</code>\n\n"
        f"🗓 {html.quote(dt_str)}\n👥 {people} чел.\n💬 {html.quote(comment_out)}"
    )
    await send_to_demo_audience(message.bot, msg1, include_admin=True)
    await state.clear()


# -------------------------
# Menu edit (админ)
# -------------------------
def _menu_as_text() -> str:
    if not MENU:
        return "Меню пока пустое."
    return "\n".join([f"• <b>{html.quote(k)}</b> — {v}₽" for k, v in MENU.items()])


@router.message(F.text == BTN_MENU_EDIT)
async def menu_edit_entry(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)

    if message.from_user.id != ADMIN_ID:
        await message.answer("🛠 Управление меню доступно админу.\n\n" + _menu_as_text(), reply_markup=create_main_keyboard())
        return

    await state.clear()
    await state.set_state(MenuEditStates.waiting_for_action)
    await message.answer("🛠 Управление меню\n\n" + _menu_as_text(), reply_markup=create_menu_edit_keyboard())


@router.message(StateFilter(MenuEditStates.waiting_for_action))
async def menu_edit_choose_action(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    if message.text == BTN_BACK:
        await state.clear()
        await message.answer("Ок.", reply_markup=create_main_keyboard())
        return

    if message.text == MENU_EDIT_ADD:
        await state.set_state(MenuEditStates.waiting_for_add_name)
        await message.answer("Название новой позиции:", reply_markup=create_menu_edit_cancel_keyboard())
        return

    if message.text == MENU_EDIT_EDIT:
        await state.set_state(MenuEditStates.waiting_for_edit_name)
        await message.answer("Название позиции для смены цены:\n\n" + _menu_as_text(), reply_markup=create_menu_edit_cancel_keyboard())
        return

    if message.text == MENU_EDIT_DEL:
        await state.set_state(MenuEditStates.waiting_for_remove_name)
        await message.answer("Название позиции для удаления:\n\n" + _menu_as_text(), reply_markup=create_menu_edit_cancel_keyboard())
        return

    await message.answer("Выберите действие кнопкой.", reply_markup=create_menu_edit_keyboard())


@router.message(StateFilter(MenuEditStates.waiting_for_add_name))
async def menu_edit_add_name(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    if message.text == BTN_BACK:
        await state.set_state(MenuEditStates.waiting_for_action)
        await message.answer("Выберите действие:", reply_markup=create_menu_edit_keyboard())
        return
    name = (message.text or "").strip()
    if not name or _is_reserved_button(name):
        await message.answer("Некорректное название.", reply_markup=create_menu_edit_cancel_keyboard())
        return
    await state.update_data(add_name=name)
    await state.set_state(MenuEditStates.waiting_for_add_price)
    await message.answer("Цена числом:", reply_markup=create_menu_edit_cancel_keyboard())


@router.message(StateFilter(MenuEditStates.waiting_for_add_price))
async def menu_edit_add_price(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    if message.text == BTN_BACK:
        await state.set_state(MenuEditStates.waiting_for_action)
        await message.answer("Выберите действие:", reply_markup=create_menu_edit_keyboard())
        return
    try:
        price = int((message.text or "").strip())
        if price <= 0 or price > 100000:
            raise ValueError
    except Exception:
        await message.answer("Цена должна быть числом.", reply_markup=create_menu_edit_cancel_keyboard())
        return
    data = await state.get_data()
    name = (data.get("add_name") or "").strip()
    await menu_set_item(name, price)
    await state.clear()
    await message.answer("✅ Добавлено.\n\n" + _menu_as_text(), reply_markup=create_main_keyboard())


@router.message(StateFilter(MenuEditStates.waiting_for_edit_name))
async def menu_edit_edit_name(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    if message.text == BTN_BACK:
        await state.set_state(MenuEditStates.waiting_for_action)
        await message.answer("Выберите действие:", reply_markup=create_menu_edit_keyboard())
        return
    name = (message.text or "").strip()
    if name not in MENU:
        await message.answer("Не нашёл такую позицию.", reply_markup=create_menu_edit_cancel_keyboard())
        return
    await state.update_data(edit_name=name)
    await state.set_state(MenuEditStates.waiting_for_edit_price)
    await message.answer(f"Новая цена для <b>{html.quote(name)}</b>:", reply_markup=create_menu_edit_cancel_keyboard())


@router.message(StateFilter(MenuEditStates.waiting_for_edit_price))
async def menu_edit_edit_price(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    if message.text == BTN_BACK:
        await state.set_state(MenuEditStates.waiting_for_action)
        await message.answer("Выберите действие:", reply_markup=create_menu_edit_keyboard())
        return
    try:
        price = int((message.text or "").strip())
        if price <= 0 or price > 100000:
            raise ValueError
    except Exception:
        await message.answer("Цена должна быть числом.", reply_markup=create_menu_edit_cancel_keyboard())
        return
    data = await state.get_data()
    name = (data.get("edit_name") or "").strip()
    await menu_set_item(name, price)
    await state.clear()
    await message.answer("✅ Цена изменена.\n\n" + _menu_as_text(), reply_markup=create_main_keyboard())


@router.message(StateFilter(MenuEditStates.waiting_for_remove_name))
async def menu_edit_remove(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    if message.text == BTN_BACK:
        await state.set_state(MenuEditStates.waiting_for_action)
        await message.answer("Выберите действие:", reply_markup=create_menu_edit_keyboard())
        return
    name = (message.text or "").strip()
    if name not in MENU:
        await message.answer("Не нашёл такую позицию.", reply_markup=create_menu_edit_cancel_keyboard())
        return
    await menu_delete_item(name)
    await state.clear()
    await message.answer("🗑 Удалено.\n\n" + _menu_as_text(), reply_markup=create_main_keyboard())


# -------------------------
# Drink selection + fallback text
# -------------------------
@router.message(F.text)
async def any_text(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)
    text = (message.text or "").strip()

    if text in MENU:
        if not is_cafe_open():
            await message.answer(get_closed_message(), reply_markup=create_info_keyboard())
            return
        await _start_add_item(message, state, text)
        return

    if _is_reserved_button(text):
        return

    await message.answer(
        "Чтобы добавить в корзину: нажмите напиток → выберите количество.\n"
        "Если пропали кнопки на iOS — нажмите /start.",
        reply_markup=create_main_keyboard(),
    )


# -------------------------
# Startup / Webhook
# -------------------------
_smart_return_task: Optional[asyncio.Task] = None


async def on_startup(bot: Bot) -> None:
    global _smart_return_task

    logger.info("🚀 Запуск бота...")
    logger.info(f"☕ Кафе: {CAFE_NAME}")
    logger.info(f"📍 Адрес: {CAFE_ADDRESS}")
    logger.info(f"⏰ Часы: {WORK_START}:00–{WORK_END}:00 (МСК)")
    logger.info(f"🔗 Webhook: {WEBHOOK_URL}")

    await sync_menu_from_redis()

    try:
        if _smart_return_task is None or _smart_return_task.done():
            _smart_return_task = asyncio.create_task(smart_return_loop(bot))
    except Exception:
        pass

    try:
        await bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET)
    except Exception as e:
        logger.error(f"❌ Webhook ошибка: {e}")


async def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не найден!")
        return
    if not REDIS_URL:
        logger.error("❌ REDIS_URL не найден!")
        return

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    storage = RedisStorage.from_url(REDIS_URL)
    dp = Dispatcher(storage=storage)
    dp.include_router(router)

    dp.startup.register(on_startup)

    app = web.Application()

    async def healthcheck(request: web.Request):
        return web.json_response({"status": "healthy", "bot": "ready"})

    app.router.add_get("/", healthcheck)

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
        handle_in_background=True,
    ).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    async def _on_shutdown(a: web.Application):
        global _smart_return_task
        try:
            if _smart_return_task and not _smart_return_task.done():
                _smart_return_task.cancel()
        except Exception:
            pass
        try:
            await bot.delete_webhook()
        except Exception:
            pass
        try:
            await storage.close()
        except Exception:
            pass
        try:
            await bot.session.close()
        except Exception:
            pass

    app.on_shutdown.append(_on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
