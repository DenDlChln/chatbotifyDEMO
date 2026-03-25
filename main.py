import os
import json
import logging
import asyncio
import time
import random
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple
import base64

import redis.asyncio as redis
from aiohttp import web

from aiogram import Bot, Dispatcher, F, Router, html
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

import uuid
import httpx  # не забудь в requirements.txt: httpx>=0.27.0,<1.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

MSK_TZ = timezone(timedelta(hours=3))
RATE_LIMIT_SECONDS = 60

# --- DEMO mode ---
DEMO_MODE = True  # в клиентской версии можно будет выключить

# --- paths / persistence ---
DATA_DIR = os.getenv("DATA_DIR", "/data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

# --- Redis keys ---
MENU_REDIS_KEY = "menu:items"  # hash: {drink_name: price}

# Stats keys
STATS_TOTAL_ORDERS = "stats:total_orders"
STATS_TOTAL_REVENUE = "stats:total_revenue"
STATS_DRINK_PREFIX = "stats:drink:"
STATS_DRINK_REV_PREFIX = "stats:drink_revenue:"

# Per-user "repeat last order"
LAST_SEEN_KEY_PREFIX = "last_seen:"   # string timestamp
LAST_ORDER_KEY_PREFIX = "last_order:" # string json snapshot

# Drafts for payment notifications (admin approves sending to user)
PAY_DRAFT_PREFIX = "paydraft:"  # key -> json

def _pay_draft_key(draft_id: str) -> str:
    return f"{PAY_DRAFT_PREFIX}{draft_id}"

# Smart return
CUSTOMERS_SET_KEY = "customers:set"
CUSTOMER_KEY_PREFIX = "customer:"
CUSTOMER_DRINKS_PREFIX = "customer:drinks:"

DEFAULT_RETURN_CYCLE_DAYS = 7
RETURN_COOLDOWN_DAYS = 30
RETURN_CHECK_EVERY_SECONDS = 6 * 60 * 60
RETURN_SEND_FROM_HOUR = 10
RETURN_SEND_TO_HOUR = 20
RETURN_DISCOUNT_PERCENT = 10

# Подписки Cafebotify
SUBS_CHECK_EVERY_SECONDS = 24 * 60 * 60  # раз в сутки
SUBS_REMIND_DAYS_BEFORE = 3


def get_moscow_time() -> datetime:
    return datetime.now(MSK_TZ)


def _parse_work_hours(obj: Any) -> Optional[Tuple[int, int]]:
    try:
        if isinstance(obj, list) and len(obj) == 2:
            s, e = int(obj[0]), int(obj[1])
            if 0 <= s <= 23 and 0 <= e <= 23 and s != e:
                return s, e
    except Exception:
        return None
    return None


def load_config() -> Dict[str, Any]:
    default_config = {
        "name": "Кофейня «Уют» ☕",
        "phone": "+7 989 000-00-00",
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

    # гарантируем, что /data существует
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception:
        pass

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            cafe = data.get("cafe", {})

            default_config.update(
                name=cafe.get("name", default_config["name"]),
                phone=cafe.get("phone", default_config["phone"]),
                admin_chat_id=cafe.get("admin_chat_id", default_config["admin_chat_id"]),
                address=cafe.get("address", default_config["address"]),
                menu=cafe.get("menu", default_config["menu"]),
                return_cycle_days=int(cafe.get("return_cycle_days", default_config["return_cycle_days"])),
            )

            wh = _parse_work_hours(cafe.get("work_hours"))
            if wh:
                default_config["work_start"], default_config["work_end"] = wh
            else:
                try:
                    default_config["work_start"] = int(cafe.get("work_start", default_config["work_start"]))
                    default_config["work_end"] = int(cafe.get("work_end", default_config["work_end"]))
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

# суперадмин (можешь вынести в отдельную переменную/ENV)
SUPERADMIN_ID = ADMIN_ID

BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "cafebot123")
HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME", "demo-cafebotify-denvyd.amvera.io")
PORT = int(os.getenv("PORT", 10000))

WEBHOOK_PATH = f"/{WEBHOOK_SECRET}/webhook"
WEBHOOK_URL = f"https://{HOSTNAME}{WEBHOOK_PATH}"

DEFAULT_CAFE_CODE = "cafe_001"  # меняешь только это

# ЮKassa
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
RETURN_URL = os.getenv("YOOKASSA_RETURN_URL", "https://cafebotify.tilda.ws/pay-success")

# URL лендингов Tilda (30 и 360 дней)
PAY_LANDING_MONTH = os.getenv("PAY_LANDING_MONTH", "https://cafebotify.tilda.ws/pay-30")
PAY_LANDING_YEAR = os.getenv("PAY_LANDING_YEAR", "https://cafebotify.tilda.ws/pay-360")
SITE_URL = os.getenv("SITE_URL", "https://cafebotify.tilda.ws/")

# --- dynamic cafe code / links ---
BOT_PUBLIC_USERNAME = os.getenv("BOT_PUBLIC_USERNAME", "cafebotifySTARTBOT")
DEFAULT_CAFE_CODE = os.getenv("DEFAULT_CAFE_CODE", "cafe_001")

def _b64_nopad(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")

def build_links_text(cafe_code: str) -> str:
    client = _b64_nopad(cafe_code)
    admin = _b64_nopad(f"admin:{cafe_code}")
    group = _b64_nopad(cafe_code)

    return (
        "<b>Ссылки</b>\n"
        f"• Клиентам: <a href=\"https://t.me/{BOT_PUBLIC_USERNAME}?start={client}\">"
        f"https://t.me/{BOT_PUBLIC_USERNAME}?start={client}</a>\n"
        f"• Админу: <a href=\"https://t.me/{BOT_PUBLIC_USERNAME}?start={admin}\">"
        f"https://t.me/{BOT_PUBLIC_USERNAME}?start={admin}</a>\n"
        f"• В staff-группу: <a href=\"https://t.me/{BOT_PUBLIC_USERNAME}?startgroup={group}\">"
        f"https://t.me/{BOT_PUBLIC_USERNAME}?startgroup={group}</a>\n\n"
        "В staff-группе выполните:\n"
        "<code>/bind </code><i>(дописать код вручную)</i>"
    )

router = Router()


# ---------------- Redis ----------------
async def get_redis_client():
    return redis.from_url(REDIS_URL, decode_responses=True)

def k_admin_subscription(cafe_id: str) -> str:
    return f"cafe:{cafe_id}:admin_subscription"

def k_cafe_profile(cafe_id: str) -> str:
    return f"cafe:{cafe_id}:profile"

def k_staff_group(cafe_id: str) -> str:
    return f"cafe:{cafe_id}:staff_group_id"

async def get_effective_admin_id(r: redis.Redis, cafe_id: str) -> int:
    try:
        raw = await r.hget(k_cafe_profile(cafe_id), "admin_id")
        if raw is not None and str(raw).strip() != "":
            return int(raw)
    except Exception:
        pass
    return ADMIN_ID


async def find_free_cafe_id(r: redis.Redis) -> Optional[str]:
    keys = await r.keys("cafe:*:profile")
    now_ts = int(time.time())

    for key in keys:
        try:
            parts = key.split(":")
            if len(parts) < 3:
                continue

            cafe_id = parts[1]
            admin_id_raw = await r.hget(key, "admin_id")
            if admin_id_raw and str(admin_id_raw).strip() not in ("", "0"):
                continue

            sub = await r.hgetall(k_admin_subscription(cafe_id))
            paid_flag = str(sub.get("cafebotify_paid", "0")).strip()
            valid_until_raw = str(sub.get("cafebotify_valid_until", "0")).strip()

            try:
                valid_until_ts = int(valid_until_raw or 0)
            except Exception:
                valid_until_ts = 0

            if paid_flag == "1" or valid_until_ts > now_ts:
                continue

            return cafe_id
        except Exception:
            continue

    return None


async def get_bound_active_cafe_id_by_admin(r: redis.Redis, admin_tg_id: int) -> Optional[str]:
    keys = await r.keys("cafe:*:profile")
    now_ts = int(time.time())

    for key in keys:
        try:
            parts = key.split(":")
            if len(parts) < 3:
                continue

            candidate_cafe_id = parts[1]
            admin_id_raw = await r.hget(key, "admin_id")
            if not admin_id_raw or int(admin_id_raw) != admin_tg_id:
                continue

            sub = await r.hgetall(k_admin_subscription(candidate_cafe_id))
            paid_flag = str(sub.get("cafebotify_paid", "0")).strip()
            valid_until_raw = str(sub.get("cafebotify_valid_until", "0")).strip()

            try:
                valid_until_ts = int(valid_until_raw or 0)
            except Exception:
                valid_until_ts = 0

            if paid_flag == "1" and valid_until_ts > now_ts:
                return candidate_cafe_id
        except Exception:
            continue

    return None
    

async def has_active_bound_cafe_by_admin(r: redis.Redis, admin_tg_id: int) -> bool:
    cafe_id = await get_bound_active_cafe_id_by_admin(r, admin_tg_id)
    return bool(cafe_id)

def _rate_limit_key(user_id: int) -> str:
    return f"rate_limit:{user_id}"


def _last_seen_key(user_id: int) -> str:
    return f"{LAST_SEEN_KEY_PREFIX}{user_id}"


def _last_order_key(user_id: int) -> str:
    return f"{LAST_ORDER_KEY_PREFIX}{user_id}"


async def is_user_paid(user_id: int) -> bool:
    try:
        r = await get_redis_client()
        val = await r.hget(f"user:{user_id}", "cafebotify_paid")
        await r.aclose()
        return val == "1"
    except Exception:
        return False


# ---------------- States ----------------
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
    pick_edit_item = State()
    waiting_for_edit_price = State()
    pick_remove_item = State()


class PaylinksStates(StatesGroup):
    waiting_for_cafe_id = State()
    waiting_for_preview_approve = State()


# ---------------- Working hours ----------------
def is_cafe_open() -> bool:
    return WORK_START <= get_moscow_time().hour < WORK_END


def get_work_status() -> str:
    h = get_moscow_time().hour
    if is_cafe_open():
        return f"🟢 <b>Открыто</b> (до {WORK_END}:00 МСК)"
    return f"🔴 <b>Закрыто</b>\n🕐 Открываемся: {WORK_START}:00 (МСК)"


def _address_line() -> str:
    return f"\n📍 <b>Адрес:</b> {html.quote(CAFE_ADDRESS)}" if CAFE_ADDRESS else ""


def get_closed_message() -> str:
    menu_text = " • ".join([f"<b>{html.quote(d)}</b> {p}₽" for d, p in MENU.items()])
    return (
        f"🔒 <b>{html.quote(CAFE_NAME)} сейчас закрыто!</b>\n\n"
        f"⏰ {get_work_status()}{_address_line()}\n\n"
        f"☕ <b>Меню:</b>\n{menu_text}\n\n"
        f"📞 <b>Телефон:</b> <code>{html.quote(CAFE_PHONE)}</code>"
    )


def get_user_name(message: Message) -> str:
    return (message.from_user.first_name if message.from_user else None) or "друг"


# ---------------- Admin notify ----------------
async def send_admin_only(bot: Bot, text: str):
    try:
        await bot.send_message(ADMIN_ID, text, disable_web_page_preview=True)
    except Exception:
        pass


async def send_admin_demo_to_user(bot: Bot, user_id: int, admin_like_text: str):
    if not DEMO_MODE:
        return
    demo_text = "ℹ️ <b>DEMO</b>: так это увидит админ:\n\n" + admin_like_text
    try:
        await bot.send_message(user_id, demo_text, disable_web_page_preview=True)
    except Exception:
        pass


# ---------------- Menu sync ----------------
async def sync_menu_from_redis():
    global MENU
    try:
        r = await get_redis_client()
        data = await r.hgetall(MENU_REDIS_KEY)
        if data:
            new_menu: Dict[str, int] = {}
            for k, v in data.items():
                try:
                    new_menu[str(k)] = int(v)
                except Exception:
                    continue
            if new_menu:
                MENU = new_menu
        else:
            if MENU:
                await r.hset(MENU_REDIS_KEY, mapping={k: str(v) for k, v in MENU.items()})
        await r.aclose()
    except Exception as e:
        logger.error(f"sync_menu_from_redis: {e}")


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


# ---------------- Repeat last order offer ----------------
BTN_REPEAT_LAST = "🔁 Повторить последний заказ"
BTN_REPEAT_NO = "❌ Нет, спасибо"


def create_repeat_offer_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_REPEAT_LAST), KeyboardButton(text=BTN_REPEAT_NO)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def set_last_seen(user_id: int):
    try:
        r = await get_redis_client()
        await r.set(_last_seen_key(user_id), str(time.time()))
        await r.aclose()
    except Exception:
        pass


async def should_offer_repeat(user_id: int) -> bool:
    try:
        r = await get_redis_client()
        last_seen = await r.get(_last_seen_key(user_id))
        last_order = await r.get(_last_order_key(user_id))
        await r.aclose()
    except Exception:
        return False

    if not last_order or not last_seen:
        return False

    try:
        last_seen_dt = datetime.fromtimestamp(float(last_seen), tz=MSK_TZ)
    except Exception:
        return False

    return last_seen_dt.date() != get_moscow_time().date()


async def get_last_order_snapshot(user_id: int) -> Optional[dict]:
    try:
        r = await get_redis_client()
        raw = await r.get(_last_order_key(user_id))
        await r.aclose()
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def set_last_order_snapshot(user_id: int, snapshot: dict):
    try:
        r = await get_redis_client()
        await r.set(_last_order_key(user_id), json.dumps(snapshot, ensure_ascii=False))
        await r.aclose()
    except Exception:
        pass


# ---------------- Cart helpers ----------------
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
    return sum(int(MENU.get(d, 0)) * int(q) for d, q in cart.items())


def _cart_lines(cart: Dict[str, int]) -> list[str]:
    lines = []
    for d, q in cart.items():
        p = int(MENU.get(d, 0))
        lines.append(f"• {html.quote(d)} × {q} = <b>{p * int(q)}₽</b>")
    return lines


def _cart_text(cart: Dict[str, int]) -> str:
    if not cart:
        return "🛒 <b>Корзина пустая</b>\n\nЧтобы добавить: нажмите напиток → выберите количество."
    return "🛒 <b>Ваш заказ:</b>\n" + "\n".join(_cart_lines(cart)) + f"\n\n💰 Итого: <b>{_cart_total(cart)}₽</b>"


async def _show_cart(message: Message, state: FSMContext):
    cart = _get_cart(await state.get_data())
    await state.set_state(OrderStates.cart_view)
    await state.update_data(cart=cart)
    await message.answer(_cart_text(cart), reply_markup=create_cart_keyboard(bool(cart)))


# ---------------- Buttons ----------------
BTN_CLIENT_MENU = "🍽 Меню клиента"
BTN_OWNER_MENU = "🧑‍💼 Меню владельца"
BTN_ABOUT_ASSISTANT = "🤖 О помощнике CafeBotify"

BTN_CALL = "📞 Позвонить"
BTN_HOURS = "⏰ Часы работы"
BTN_STATS = "📊 Статистика"
BTN_BOOKING = "📅 Бронирование"
BTN_MENU_EDIT = "🛠 Меню"

BTN_STAFF_GROUP = "👥 Группа персонала"
BTN_LINKS = "🔗 Ссылки"
BTN_RENEW_SUB = "💳 Продлить подписку"
BTN_SUBSCRIPTION = "🗓 Подписка"
BTN_ADS = "📢 Реклама"
BTN_BROADCAST = "📣 Рассылка"
BTN_ADMIN_HELP = "ℹ️ Справка админа"
BTN_SUPPORT = "🛟 Поддержка"

BTN_CART = "🛒 Корзина"
BTN_CHECKOUT = "✅ Оформить"
BTN_CLEAR_CART = "🧹 Очистить"
BTN_CANCEL_ORDER = "❌ Отменить заказ"
BTN_EDIT_CART = "✏️ Изменить"

BTN_CANCEL = "🔙 Отмена"
BTN_BACK = "⬅️ Назад"
BTN_TO_START = "🏠 Главное меню"
BTN_TO_CLIENT_MODE = "⬅️ В клиентский режим"

BTN_CONFIRM = "Подтвердить"
BTN_READY_NOW = "🚶 Сейчас"
BTN_READY_20 = "⏱ Через 20 мин"

CART_ACT_PLUS = "➕ +1"
CART_ACT_MINUS = "➖ -1"
CART_ACT_DEL = "🗑 Удалить"
CART_ACT_DONE = "✅ Готово"

MENU_EDIT_ADD = "➕ Добавить позицию"
MENU_EDIT_EDIT = "✏️ Изменить цену"
MENU_EDIT_DEL = "🗑 Удалить позицию"

BTN_PAY_MONTH = "💳 Оплатить 30 дней"
BTN_PAY_YEAR = "💳 Оплатить 360 дней"


# ---------------- Keyboards ----------------
def create_start_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CLIENT_MENU)],
            [KeyboardButton(text=BTN_OWNER_MENU)],
            [KeyboardButton(text=BTN_ABOUT_ASSISTANT)],
            [KeyboardButton(text=BTN_PAY_MONTH), KeyboardButton(text=BTN_PAY_YEAR)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def create_client_menu_keyboard() -> ReplyKeyboardMarkup:
    kb: list[list[KeyboardButton]] = []

    for drink in MENU.keys():
        kb.append([KeyboardButton(text=drink)])

    kb.append([
        KeyboardButton(text=BTN_CART),
        KeyboardButton(text=BTN_CHECKOUT),
        KeyboardButton(text=BTN_BOOKING),
    ])
    kb.append([
        KeyboardButton(text=BTN_CALL),
        KeyboardButton(text=BTN_HOURS),
    ])
    kb.append([KeyboardButton(text=BTN_TO_START)])

    return ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True,
        is_persistent=True,
    )


def create_owner_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_MENU_EDIT)],
            [KeyboardButton(text=BTN_STAFF_GROUP), KeyboardButton(text=BTN_LINKS)],
            [KeyboardButton(text=BTN_RENEW_SUB), KeyboardButton(text=BTN_SUBSCRIPTION)],
            [KeyboardButton(text=BTN_ADS), KeyboardButton(text=BTN_BROADCAST)],
            [KeyboardButton(text=BTN_ADMIN_HELP), KeyboardButton(text=BTN_SUPPORT)],
            [KeyboardButton(text=BTN_TO_CLIENT_MODE)],
            [KeyboardButton(text=BTN_TO_START)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def create_cart_keyboard(cart_has_items: bool) -> ReplyKeyboardMarkup:
    kb: list[list[KeyboardButton]] = []

    kb.append([KeyboardButton(text=BTN_CART), KeyboardButton(text=BTN_CHECKOUT)])

    if cart_has_items:
        kb.append([
            KeyboardButton(text=BTN_EDIT_CART),
            KeyboardButton(text=BTN_CLEAR_CART),
            KeyboardButton(text=BTN_CANCEL_ORDER),
        ])
    else:
        kb.append([KeyboardButton(text=BTN_CANCEL_ORDER)])

    for drink in MENU.keys():
        kb.append([KeyboardButton(text=drink)])

    kb.append([
        KeyboardButton(text=BTN_BOOKING),
        KeyboardButton(text=BTN_CALL),
        KeyboardButton(text=BTN_HOURS),
    ])
    kb.append([KeyboardButton(text=BTN_TO_START)])

    return ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True,
        is_persistent=True,
    )


def create_quantity_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1️⃣"), KeyboardButton(text="2️⃣"), KeyboardButton(text="3️⃣")],
            [KeyboardButton(text="4️⃣"), KeyboardButton(text="5️⃣"), KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def create_confirm_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CONFIRM), KeyboardButton(text=BTN_CART)],
            [KeyboardButton(text=BTN_CANCEL_ORDER)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def create_ready_time_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_READY_NOW), KeyboardButton(text=BTN_READY_20)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def create_cart_pick_item_keyboard(cart: Dict[str, int]) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [[KeyboardButton(text=k)] for k in cart.keys()]
    rows.append([KeyboardButton(text=BTN_CANCEL), KeyboardButton(text=BTN_CART)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def create_cart_edit_actions_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=CART_ACT_PLUS), KeyboardButton(text=CART_ACT_MINUS)],
            [KeyboardButton(text=CART_ACT_DEL), KeyboardButton(text=CART_ACT_DONE)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def create_booking_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def create_booking_people_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1"), KeyboardButton(text="2"), KeyboardButton(text="3"), KeyboardButton(text="4")],
            [KeyboardButton(text="5"), KeyboardButton(text="6"), KeyboardButton(text="7"), KeyboardButton(text="8")],
            [KeyboardButton(text="9"), KeyboardButton(text="10"), KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def create_menu_edit_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU_EDIT_ADD), KeyboardButton(text=MENU_EDIT_EDIT)],
            [KeyboardButton(text=MENU_EDIT_DEL), KeyboardButton(text=BTN_BACK)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def create_menu_edit_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_BACK)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def create_pick_menu_item_keyboard() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=k)] for k in MENU.keys()]
    rows.append([KeyboardButton(text=BTN_BACK)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ---- Хендлер: кнопка «🍽 Меню клиента» ----
@router.message(F.text == BTN_CLIENT_MENU)
async def open_client_menu(message: Message, state: FSMContext):
    await state.clear()
    await sync_menu_from_redis()

    await message.answer(
        "🍽 <b>Меню клиента</b>\n\n"
        "Выберите напиток из списка ниже, чтобы добавить его в корзину.\n"
        "Когда будете готовы — перейдите в корзину, чтобы оформить заказ.",
        reply_markup=create_client_menu_keyboard(),  # клавиатура с напитками, корзиной и «🏠 Главное меню»
    )


@router.message(F.text == BTN_OWNER_MENU)
async def open_owner_menu(message: Message, state: FSMContext):
    await state.clear()

    await message.answer(
        "🧑💼 <b>Меню владельца кафе</b>\n\n"
        "Здесь вы можете управлять ассистентом CafeBotify: смотреть статистику, "
        "редактировать меню и настраивать приём заказов.",
        reply_markup=create_owner_menu_keyboard(),
    )


# ---- Хендлер: кнопка «🏠 Главное меню» (BTN_TO_START) ----
@router.message(F.text == BTN_TO_START)
async def back_to_start(message: Message, state: FSMContext):
    await state.clear()
    await sync_menu_from_redis()

    await message.answer(
        "🏠 Вы вернулись в главное меню.\n\n"
        "Выберите, как хотите продолжить:",
        reply_markup=create_start_keyboard(),  # наши 4+ стартовые кнопки
    )


# ---------------- DEMO: examples for client ----------------
def demo_menu_edit_preview_text() -> str:
    return (
        "🛠 <b>Управление меню (DEMO-пример)</b>\n\n"
        "Так будет выглядеть редактор меню после покупки бота.\n"
        "Изменения доступны только администратору.\n\n"
        "Пример действий:\n"
        "• ➕ Добавить позицию\n"
        "• ✏️ Изменить цену\n"
        "• 🗑 Удалить позицию"
    )


def demo_stats_preview_text() -> str:
    return (
        "📊 <b>Статистика (DEMO-пример)</b>\n\n"
        "Так будет выглядеть отчёт у владельца (админа) после покупки.\n\n"
        "Всего заказов: <b>128</b>\n"
        "Выручка всего: <b>34 560₽</b>\n\n"
        "<b>По позициям:</b>\n"
        "• ☕ Капучино: <b>54</b> шт., <b>13 500₽</b>\n"
        "• 🥛 Латте: <b>41</b> шт., <b>11 070₽</b>\n"
        "• 🍵 Чай: <b>22</b> шт., <b>3 960₽</b>\n"
        "• ⚡ Эспрессо: <b>11</b> шт., <b>2 200₽</b>"
    )


def owner_staff_group_text() -> str:
    return (
        "👥 <b>Группа персонала</b>\n\n"
        "Здесь владелец сможет подключить рабочий чат команды, "
        "куда бот будет отправлять новые заказы и бронирования.\n\n"
        "Что будет доступно:\n"
        "• уведомления о новых заказах;\n"
        "• общий чат для сотрудников смены;\n"
        "• быстрое подключение персонала без доступа к настройкам владельца."
    )


def owner_links_text() -> str:
    return (
        "🔗 <b>Ссылки</b>\n\n"
        "В этом разделе будут собраны все ссылки вашего кафе:\n"
        "• клиентская ссылка на бота;\n"
        "• ссылка для владельца;\n"
        "• ссылка для staff-группы;\n"
        "• служебные подключения.\n\n"
        "Это удобно для размещения в соцсетях, на сайте и в QR-кодах."
    )


def owner_renew_subscription_text() -> str:
    return (
        "💳 <b>Продлить подписку</b>\n\n"
        "Здесь владелец сможет быстро продлить доступ к боту без лишних действий.\n\n"
        "Обычно это включает:\n"
        "• оплату на 30 дней;\n"
        "• оплату на 360 дней;\n"
        "• автоматическое продление доступа после успешной оплаты."
    )


def owner_subscription_text() -> str:
    return (
        "🗓 <b>Подписка</b>\n\n"
        "В этом разделе отображается информация по текущему тарифу кафе:\n"
        "• активна ли подписка;\n"
        "• до какой даты действует доступ;\n"
        "• какой период оплачен;\n"
        "• когда потребуется продление."
    )


def owner_ads_text() -> str:
    return (
        "📢 <b>Реклама</b>\n\n"
        "Этот раздел поможет кафе привлекать новых гостей и возвращать тех, кто давно не заходил.\n\n"
        "Что здесь может быть:\n"
        "• запуск акций и спецпредложений;\n"
        "• промокоды на напитки и десерты;\n"
        "• рекламные посты партнёров;\n"
        "• рекламные сообщения для новых и постоянных гостей;\n"
        "• отдельные офферы на утро, обед и вечер.\n\n"
        "Идея простая: не просто наливать кофе, а системно приводить людей обратно или просто получать дополнительный заработок с помощью рекламы."
    )


def owner_broadcast_text() -> str:
    return (
        "📣 <b>Рассылка</b>\n\n"
        "Этот раздел нужен для быстрых сообщений вашим клиентам прямо в Telegram.\n\n"
        "Что можно делать:\n"
        "• сообщать о новых позициях в меню;\n"
        "• запускать анонсы акций и скидок;\n"
        "• напоминать о себе в тихие дни;\n"
        "• возвращать гостей точечными предложениями.\n\n"
        "Так кафе остаётся на связи с клиентами без ручной переписки и хаоса."
    )


def owner_admin_help_text() -> str:
    return (
        "ℹ️ <b>Справка админа</b>\n\n"
        "Здесь собрана краткая инструкция для владельца кафе.\n\n"
        "Основные разделы:\n"
        "• <b>Статистика</b> — отчёты по заказам и выручке;\n"
        "• <b>Меню</b> — управление позициями и ценами;\n"
        "• <b>Группа персонала</b> — рабочий чат сотрудников;\n"
        "• <b>Ссылки</b> — быстрый доступ ко всем ссылкам бота;\n"
        "• <b>Подписка</b> — статус и срок действия сервиса."
    )


def owner_support_text() -> str:
    return (
        "🆘 <b>Поддержка</b>\n\n"
        "Этот раздел нужен для связи с поддержкой Cafebotify.\n\n"
        "Здесь владелец сможет:\n"
        "• получить помощь по настройке;\n"
        "• сообщить о проблеме;\n"
        "• уточнить вопросы по оплате и подписке;\n"
    )


def owner_client_mode_text() -> str:
    return (
        "⬅️ <b>В клиентский режим</b>\n\n"
        "Эта кнопка показывает, как выглядит клиентская часть бота для гостей кафе.\n\n"
        "Здесь можно:\n"
        "• посмотреть меню глазами клиента;\n"
        "• проверить путь заказа и корзину;\n"
        "• протестировать бронирование;\n"
        "• убедиться, что клиентский сценарий работает удобно и понятно.\n\n"
        "Это полезно для владельца: вы сразу видите сервис так, как его видит гость."
    )


def about_assistant_text() -> str:
    return (
        "🤖 <b>CafeBotify — ваш цифровой администратор кафе</b>\n\n"
        "Пока бариста готовят кофе, CafeBotify берёт на себя рутину:\n"
        "заказы, бронирования, напоминания и общение с гостями в Telegram.\n\n"
        "💸 <b>Для владельца:</b>\n"
        "• Меньше пропущенных звонков и хаоса в мессенджерах — бот принимает заказы 24/7.\n"
        "• Чёткая стата по заказам и выручке — видно, что реально продаётся, а что занимает место в меню.\n"
        "• Автоматические напоминания гостям — бот сам возвращает людей, даже когда у вас завал.\n"
        "• Возможность рассылки клиентам с дальнейшей статистикой по переходам и покупкам.\n"
        "• Возможность зараатывать на рекламных постах или рекламировать свою продукцию.\n"
        "• Простое управление меню — цены и позиции меняются прямо из Telegram, без верстальщиков и программистов.\n\n"
        "☕ <b>Для гостей:</b>\n"
        "• Быстрый заказ без звонков и ожидания на линии.\n"
        "• Удобное бронирование столиков в пару тапов.\n"
        "• Актуальное меню, часы работы и телефон всегда под рукой.\n\n"
        "📲 CafeBotify не заменяет живой сервис — он снимает рутину с персонала, "
        "чтобы команда занималась главным: вкусным кофе и гостями в зале.\n\n"
        "Подробнее о возможностях и тарифах — на лендинге:"
    )


# ---------------- /start ----------------
WELCOME_VARIANTS = [
    "Рад тебя видеть, {name}!",
    "{name}, добро пожаловать!",
    "Привет, {name}!",
]

CHOICE_VARIANTS = [
    "Отличный выбор!",
    "Классика.",
    "Звучит вкусно!",
]

FINISH_VARIANTS = [
    "Спасибо за заказ, {name}!",
    "Принято, {name}. Заглядывай ещё!",
]


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await sync_menu_from_redis()

    user_id = message.from_user.id
    name = html.quote(get_user_name(message))
    welcome = random.choice(WELCOME_VARIANTS).format(name=name)

    offer_repeat = await should_offer_repeat(user_id)
    await set_last_seen(user_id)

    if not is_cafe_open():
        await message.answer(
            get_closed_message(),
            reply_markup=create_start_keyboard(),
        )
        return

    if offer_repeat:
        snap = await get_last_order_snapshot(user_id)
        if snap and isinstance(snap.get("cart"), dict) and snap.get("cart"):
            cart_preview = snap["cart"]
            lines = []
            for d, q in cart_preview.items():
                try:
                    lines.append(f"• {html.quote(str(d))} × {int(q)}")
                except Exception:
                    continue

            await state.update_data(repeat_offer_snapshot=snap)
            await message.answer(
                f"{welcome}\n\nВы давно не заходили. Повторить последний заказ?\n\n" + "\n".join(lines),
                reply_markup=create_repeat_offer_keyboard(),
            )
            return

    await message.answer(
        f"{welcome}\n\n"
        f"🏪 {get_work_status()}{_address_line()}\n\n"
        "Выберите раздел ниже:\n"
        "• Меню клиента — для заказа и бронирования\n"
        "• Меню владельца — для просмотра функций управления",
        reply_markup=create_start_keyboard(),
    )


@router.message(Command("pingtest"))
async def pingtest(message: Message):
    await message.answer("PINGTEST_OK")


@router.message(Command("webhookinfo"))
async def webhookinfo(message: Message):
    info = await message.bot.get_webhook_info()
    await message.answer(
        f"url: <code>{html.quote(info.url or '-')}</code>\n"
        f"pending: <code>{info.pending_update_count}</code>\n"
        f"last_error_date: <code>{html.quote(str(info.last_error_date))}</code>\n"
        f"last_error_message: <code>{html.quote(str(info.last_error_message))}</code>",
        parse_mode="HTML",
    )


@router.message(Command("myid"))
async def myid_cmd(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    await message.answer(
        "🆔 <b>Ваш Telegram ID</b>\n\n"
        f"<code>{user_id}</code>\n\n"
        "Скопируйте этот ID и укажите его в форме на лендинге "
        "в поле «ID администратора (Telegram)»."
        + (f"\n\n🔗 Username: @{username}" if username else "")
    )


@router.message(Command("botstart"))
async def botstart_cmd(message: Message):
    text = (
        "<b>CafebotifySTART</b>\n\n"
        "🚀 Start — это:\n"
        "• Заказы без комиссии\n"
        "• Бронирование 24/7\n"
        "• Статистика продаж\n"
        "• Возврат клиентов (умные напоминания)\n"
        "• Запуск за 1 день\n\n"
        f"🌐 Сайт: {SITE_URL}"
    )
    await message.answer(text, disable_web_page_preview=True, reply_markup=create_start_keyboard())


@router.message(F.text == BTN_REPEAT_NO)
async def repeat_no(message: Message, state: FSMContext):
    await state.update_data(repeat_offer_snapshot=None)
    await message.answer("Ок.", reply_markup=create_start_keyboard())



@router.message(F.text == BTN_REPEAT_LAST)
async def repeat_last(message: Message, state: FSMContext):
    data = await state.get_data()
    snap = data.get("repeat_offer_snapshot") or await get_last_order_snapshot(message.from_user.id)

    if not snap or not isinstance(snap.get("cart"), dict) or not snap.get("cart"):
        await message.answer("Не нашёл последний заказ.", reply_markup=create_start_keyboard())
        return

    cart = {}
    for k, v in snap["cart"].items():
        try:
            cart[str(k)] = int(v)
        except Exception:
            continue

    filtered = {d: q for d, q in cart.items() if d in MENU and q > 0}
    if not filtered:
        await message.answer("Позиции из прошлого заказа сейчас отсутствуют в меню.", reply_markup=create_start_keyboard())
        return

    await state.update_data(cart=filtered)
    await _show_cart(message, state)


@router.message(F.text == BTN_ABOUT_ASSISTANT)
async def about_assistant(message: Message):
    await message.answer(
        about_assistant_text() + "\n\n"
        "<a href=\"https://cafebotify.tilda.ws\">Перейти на сайт CafeBotify</a>",
        disable_web_page_preview=False,
        reply_markup=create_start_keyboard(),
    )


# ---------------- Pay buttons ----------------
@router.message(F.text == BTN_PAY_MONTH)
async def pay_month_button(message: Message):
    user_id = message.from_user.id
    url = f"{PAY_LANDING_MONTH}?tg_id={user_id}"
    text = (
        "💳 <b>Оплата доступа на 30 дней</b>\n\n"
        "1) Откройте страницу оплаты.\n"
        "2) Заполните форму (данные кафе + ваш Telegram ID).\n"
        "3) Оплатите на странице ЮKassa.\n\n"
        "После успешной оплаты доступ будет активирован автоматически."
    )
    await message.answer(
        f"{text}\n\n<a href=\"{html.quote(url)}\">Оплатить 30 дней</a>",
        reply_markup=create_start_keyboard(),
    )


@router.message(F.text == BTN_PAY_YEAR)
async def pay_year_button(message: Message):
    user_id = message.from_user.id
    url = f"{PAY_LANDING_YEAR}?tg_id={user_id}"
    text = (
        "💳 <b>Оплата доступа на 360 дней</b>\n\n"
        "1) Откройте страницу оплаты.\n"
        "2) Заполните форму.\n"
        "3) Оплатите на странице ЮKassa.\n\n"
        "После успешной оплаты доступ будет активирован автоматически."
    )
    await message.answer(
        f"{text}\n\n<a href=\"{html.quote(url)}\">Оплатить 360 дней</a>",
        reply_markup=create_start_keyboard(),
    )


# ---------------- Info buttons ----------------
@router.message(F.text == BTN_CALL)
async def call_phone(message: Message):
    await message.answer(
        f"📞 <b>Телефон:</b> <code>{html.quote(CAFE_PHONE)}</code>",
        reply_markup=create_client_menu_keyboard(),
    )


@router.message(F.text == BTN_HOURS)
async def show_hours(message: Message):
    msk_time = get_moscow_time().strftime("%H:%M")
    await message.answer(
        f"🕐 <b>Сейчас:</b> {msk_time} (МСК)\n{get_work_status()}{_address_line()}",
        reply_markup=create_client_menu_keyboard(),
    )


@router.message(F.text == BTN_STAFF_GROUP)
async def owner_staff_group(message: Message):
    await message.answer(owner_staff_group_text(), reply_markup=create_owner_menu_keyboard())


@router.message(F.text == BTN_LINKS)
async def owner_links(message: Message):
    await message.answer(owner_links_text(), reply_markup=create_owner_menu_keyboard())


@router.message(F.text == BTN_RENEW_SUB)
async def owner_renew_subscription(message: Message):
    await message.answer(owner_renew_subscription_text(), reply_markup=create_owner_menu_keyboard())


@router.message(F.text == BTN_ADS)
async def owner_ads(message: Message):
    await message.answer(owner_ads_text(), reply_markup=create_owner_menu_keyboard())


@router.message(F.text == BTN_BROADCAST)
async def owner_broadcast(message: Message):
    await message.answer(owner_broadcast_text(), reply_markup=create_owner_menu_keyboard())


@router.message(F.text == BTN_TO_CLIENT_MODE)
async def owner_client_mode_info(message: Message):
    await message.answer(
        owner_client_mode_text(),
        reply_markup=create_owner_menu_keyboard(),
    )


@router.message(F.text == BTN_SUBSCRIPTION)
async def owner_subscription(message: Message):
    await message.answer(owner_subscription_text(), reply_markup=create_owner_menu_keyboard())


@router.message(F.text == BTN_ADMIN_HELP)
async def owner_admin_help(message: Message):
    await message.answer(owner_admin_help_text(), reply_markup=create_owner_menu_keyboard())


@router.message(F.text == BTN_SUPPORT)
async def owner_support(message: Message):
    await message.answer(owner_support_text(), reply_markup=create_owner_menu_keyboard())


# ---------------- Menu edit entry (DEMO preview for non-admin) ----------------
@router.message(F.text == BTN_MENU_EDIT)
async def menu_edit_entry(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        if DEMO_MODE:
            await message.answer(demo_menu_edit_preview_text(), reply_markup=create_menu_edit_keyboard())
            await message.answer("🔒 Редактирование доступно только администратору.", reply_markup=create_owner_menu_keyboard())
        else:
            await message.answer("🔒 Редактирование доступно только администратору.", reply_markup=create_owner_menu_keyboard())
        return

    await sync_menu_from_redis()
    await state.clear()
    await state.set_state(MenuEditStates.waiting_for_action)
    await message.answer("🛠 Управление меню: выберите действие", reply_markup=create_menu_edit_keyboard())


@router.message(StateFilter(MenuEditStates.waiting_for_action))
async def menu_edit_choose_action(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    if message.text == BTN_BACK:
        await state.clear()
        await message.answer("Ок.", reply_markup=create_owner_menu_keyboard())
        return

    if message.text == MENU_EDIT_ADD:
        await state.set_state(MenuEditStates.waiting_for_add_name)
        await message.answer("Введите название новой позиции:", reply_markup=create_menu_edit_cancel_keyboard())
        return

    if message.text == MENU_EDIT_EDIT:
        await state.set_state(MenuEditStates.pick_edit_item)
        await message.answer("Выберите позицию для изменения цены:", reply_markup=create_pick_menu_item_keyboard())
        return

    if message.text == MENU_EDIT_DEL:
        await state.set_state(MenuEditStates.pick_remove_item)
        await message.answer("Выберите позицию для удаления:", reply_markup=create_pick_menu_item_keyboard())
        return

    await message.answer("Выберите действие кнопкой.", reply_markup=create_menu_edit_keyboard())


@router.message(StateFilter(MenuEditStates.waiting_for_add_name))
async def menu_edit_add_name(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    if message.text == BTN_BACK:
        await state.set_state(MenuEditStates.waiting_for_action)
        await message.answer("Ок.", reply_markup=create_menu_edit_keyboard())
        return

    name = (message.text or "").strip()
    if not name:
        await message.answer("Введите название.", reply_markup=create_menu_edit_cancel_keyboard())
        return

    await state.update_data(add_name=name)
    await state.set_state(MenuEditStates.waiting_for_add_price)
    await message.answer("Введите цену числом:", reply_markup=create_menu_edit_cancel_keyboard())


@router.message(StateFilter(MenuEditStates.waiting_for_add_price))
async def menu_edit_add_price(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    if message.text == BTN_BACK:
        await state.set_state(MenuEditStates.waiting_for_action)
        await message.answer("Ок.", reply_markup=create_menu_edit_keyboard())
        return

    try:
        price = int((message.text or "").strip())
        if price <= 0:
            raise ValueError
    except Exception:
        await message.answer("Цена должна быть числом.", reply_markup=create_menu_edit_cancel_keyboard())
        return

    data = await state.get_data()
    name = str(data.get("add_name") or "").strip()
    await menu_set_item(name, price)
    await state.clear()
    await message.answer("✅ Добавлено.", reply_markup=create_owner_menu_keyboard())


@router.message(StateFilter(MenuEditStates.pick_edit_item))
async def menu_pick_edit_item(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    if message.text == BTN_BACK:
        await state.set_state(MenuEditStates.waiting_for_action)
        await message.answer("Ок.", reply_markup=create_menu_edit_keyboard())
        return

    picked = (message.text or "").strip()
    if picked not in MENU:
        await message.answer("Выберите позицию кнопкой.", reply_markup=create_pick_menu_item_keyboard())
        return

    await state.update_data(edit_name=picked)
    await state.set_state(MenuEditStates.waiting_for_edit_price)
    await message.answer(f"Новая цена для <b>{html.quote(picked)}</b>:", reply_markup=create_menu_edit_cancel_keyboard())


@router.message(StateFilter(MenuEditStates.waiting_for_edit_price))
async def menu_edit_price(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    if message.text == BTN_BACK:
        await state.set_state(MenuEditStates.waiting_for_action)
        await message.answer("Ок.", reply_markup=create_menu_edit_keyboard())
        return

    try:
        price = int((message.text or "").strip())
        if price <= 0:
            raise ValueError
    except Exception:
        await message.answer("Цена должна быть числом.", reply_markup=create_menu_edit_cancel_keyboard())
        return

    data = await state.get_data()
    name = str(data.get("edit_name") or "")
    if name not in MENU:
        await state.clear()
        await message.answer("Позиция не найдена. /start", reply_markup=create_owner_menu_keyboard())
        return

    await menu_set_item(name, price)
    await state.clear()
    await message.answer("✅ Цена изменена.", reply_markup=create_owner_menu_keyboard())


@router.message(StateFilter(MenuEditStates.pick_remove_item))
async def menu_pick_remove_item(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    if message.text == BTN_BACK:
        await state.set_state(MenuEditStates.waiting_for_action)
        await message.answer("Ок.", reply_markup=create_menu_edit_keyboard())
        return

    picked = (message.text or "").strip()
    if picked not in MENU:
        await message.answer("Выберите позицию кнопкой.", reply_markup=create_pick_menu_item_keyboard())
        return

    await menu_delete_item(picked)
    await state.clear()
    await message.answer("🗑 Удалено.", reply_markup=create_owner_menu_keyboard())


# ---------------- Stats button (DEMO preview for non-admin) ----------------
@router.message(F.text == BTN_STATS)
async def stats_button(message: Message):
    if message.from_user.id != ADMIN_ID:
        if DEMO_MODE:
            await message.answer(demo_stats_preview_text(), reply_markup=create_owner_menu_keyboard())
        else:
            await message.answer("📊 Статистика доступна администратору.", reply_markup=create_owner_menu_keyboard())
        return

    try:
        r = await get_redis_client()
        total_orders = int(await r.get(STATS_TOTAL_ORDERS) or 0)
        total_rev = int(await r.get(STATS_TOTAL_REVENUE) or 0)

        lines = []
        for drink in MENU.keys():
            cnt = int(await r.get(f"{STATS_DRINK_PREFIX}{drink}") or 0)
            rev = int(await r.get(f"{STATS_DRINK_REV_PREFIX}{drink}") or 0)
            lines.append(f"• {html.quote(drink)}: <b>{cnt}</b> шт., <b>{rev}₽</b>")

        await r.aclose()

        text = (
            "📊 <b>Статистика</b>\n\n"
            f"Всего заказов: <b>{total_orders}</b>\n"
            f"Выручка всего: <b>{total_rev}₽</b>\n\n"
            "<b>По позициям:</b>\n" + "\n".join(lines)
        )
        await message.answer(text, reply_markup=create_owner_menu_keyboard())
    except Exception:
        await message.answer("❌ Ошибка статистики", reply_markup=create_owner_menu_keyboard())


# ---------------- Cart show/clear/cancel ----------------
@router.message(F.text == BTN_CART)
async def cart_button(message: Message, state: FSMContext):
    if not is_cafe_open():
        await message.answer(get_closed_message(), reply_markup=create_client_menu_keyboard())
        return
    await _show_cart(message, state)


@router.message(F.text == BTN_CLEAR_CART)
async def clear_cart(message: Message, state: FSMContext):
    await state.update_data(cart={})
    await _show_cart(message, state)


@router.message(F.text == BTN_CANCEL_ORDER)
async def cancel_order(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Заказ отменён.", reply_markup=create_client_menu_keyboard())


# ---------------- Cart edit ----------------
@router.message(F.text == BTN_EDIT_CART)
async def edit_cart(message: Message, state: FSMContext):
    cart = _get_cart(await state.get_data())
    if not cart:
        await message.answer("Корзина пустая.", reply_markup=create_client_menu_keyboard())
        return
    await state.set_state(OrderStates.cart_edit_pick_item)
    await message.answer("Выберите позицию:", reply_markup=create_cart_pick_item_keyboard(cart))


@router.message(F.from_user.id == ADMIN_ID, StateFilter(None))
async def admin_write_to_payer(message: Message):
    logger.info(f"ADMIN CATCHER 1 text={message.text!r}")

    if not (text := message.text) or not text.startswith("[Ответ] tgid:"):
        return

    tgid_match = re.search(r"tgid:(\d+)", text)
    if not tgid_match:
        logger.info("ADMIN CATCHER 1 no tgid found")
        return

    tgid_int = int(tgid_match.group(1))
    logger.info(f"ADMIN CATCHER 1 parsed tgid={tgid_int}")

    client_token = (os.getenv("CLIENT_BOT_TOKEN") or "").strip()
    if client_token:
        client_bot = Bot(token=client_token)
        try:
            payload_text = text.replace(f"[Ответ] tgid:{tgid_int}", "", 1).strip()
            if not payload_text:
                payload_text = text.replace("[Ответ] tgid:", "", 1).strip()

            await client_bot.send_message(
                tgid_int,
                f"💬 <b>Ответ от поддержки:</b>\n\n{html.quote(payload_text)}",
                parse_mode="HTML",
            )
            await message.answer(f"✅ Отправлено плательщику <code>{tgid_int}</code>")
            logger.info(f"ADMIN CATCHER 1 sent to tgid={tgid_int}")
        finally:
            await client_bot.session.close()
    else:
        logger.info("ADMIN CATCHER 1 CLIENT_BOT_TOKEN missing")
        await message.answer("❌ CLIENT_BOT_TOKEN не задан.")


@router.message(StateFilter(OrderStates.cart_edit_pick_item))
async def pick_item_to_edit(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text in {BTN_CANCEL, BTN_CART}:
        await _show_cart(message, state)
        return

    cart = _get_cart(await state.get_data())
    if text not in cart:
        await message.answer("Выберите позицию кнопкой.", reply_markup=create_cart_pick_item_keyboard(cart))
        return

    await state.set_state(OrderStates.cart_edit_pick_action)
    await state.update_data(edit_item=text)
    await message.answer(f"Что сделать с <b>{html.quote(text)}</b>?", reply_markup=create_cart_edit_actions_keyboard())


@router.message(StateFilter(OrderStates.cart_edit_pick_action))
async def cart_edit_action(message: Message, state: FSMContext):
    action = (message.text or "").strip()
    if action == BTN_CANCEL:
        await _show_cart(message, state)
        return

    data = await state.get_data()
    cart = _get_cart(data)
    item = str(data.get("edit_item") or "")

    if action == CART_ACT_DONE:
        await _show_cart(message, state)
        return

    if not item or item not in cart:
        await _show_cart(message, state)
        return

    if action == CART_ACT_PLUS:
        cart[item] = int(cart.get(item, 0)) + 1
    elif action == CART_ACT_MINUS:
        cart[item] = int(cart.get(item, 0)) - 1
        if cart[item] <= 0:
            cart.pop(item, None)
    elif action == CART_ACT_DEL:
        cart.pop(item, None)
    else:
        await message.answer("Выберите действие кнопкой.", reply_markup=create_cart_edit_actions_keyboard())
        return

    await state.update_data(cart=cart)
    await _show_cart(message, state)


# ---- Хендлер: кнопка «🍽 Меню клиента» ----

# ---------------- Add item: drink -> quantity ----------------
async def _start_add_item(message: Message, state: FSMContext, drink: str):
    price = MENU.get(drink)
    if price is None:
        await message.answer("Этой позиции уже нет.", reply_markup=create_client_menu_keyboard())
        return

    cart = _get_cart(await state.get_data())
    await state.set_state(OrderStates.waiting_for_quantity)
    await state.update_data(current_drink=drink, cart=cart)

    await message.answer(
        f"{random.choice(CHOICE_VARIANTS)}\n\n"
        f"🥤 <b>{html.quote(drink)}</b>\n💰 <b>{price}₽</b>\n\nСколько добавить?",
        reply_markup=create_quantity_keyboard(),
    )


@router.message(StateFilter(OrderStates.waiting_for_quantity))
async def process_quantity(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await state.clear()
        cart = _get_cart(await state.get_data())
        await message.answer(
            "Ок.",
            reply_markup=create_cart_keyboard(bool(cart)) if cart else create_client_menu_keyboard()
        )
        return

    try:
        qty = int((message.text or "")[0])
        if not (1 <= qty <= 5):
            raise ValueError
    except Exception:
        await message.answer("Нажмите 1–5.", reply_markup=create_quantity_keyboard())
        return

    data = await state.get_data()
    drink = str(data.get("current_drink") or "")
    cart = _get_cart(data)

    if not drink or drink not in MENU:
        await state.clear()
        await message.answer("Ошибка. Нажмите /start.", reply_markup=create_client_menu_keyboard())
        return

    cart[drink] = int(cart.get(drink, 0)) + qty
    await state.update_data(cart=cart)
    await state.set_state(OrderStates.cart_view)

    await message.answer(
        f"✅ Добавил в корзину: <b>{html.quote(drink)}</b> × {qty}\n\n{_cart_text(cart)}",
        reply_markup=create_cart_keyboard(True),
    )


# ---------------- Checkout ----------------
@router.message(F.text == BTN_CHECKOUT)
async def checkout(message: Message, state: FSMContext):
    if not is_cafe_open():
        await message.answer(get_closed_message(), reply_markup=create_client_menu_keyboard())
        return

    cart = _get_cart(await state.get_data())
    if not cart:
        await message.answer("Корзина пустая.", reply_markup=create_client_menu_keyboard())
        return

    await state.set_state(OrderStates.waiting_for_confirmation)
    await message.answer(
        "✅ <b>Подтвердите заказ</b>\n\n" + _cart_text(cart),
        reply_markup=create_confirm_keyboard(),
    )


@router.message(StateFilter(OrderStates.waiting_for_confirmation))
async def confirm_order(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL_ORDER:
        await state.clear()
        await message.answer("❌ Отменено.", reply_markup=create_client_menu_keyboard())
        return

    if message.text == BTN_CART:
        await _show_cart(message, state)
        return

    if message.text != BTN_CONFIRM:
        await message.answer("Нажмите «Подтвердить».", reply_markup=create_confirm_keyboard())
        return

    await state.set_state(OrderStates.waiting_for_ready_time)
    await message.answer("Когда забрать?", reply_markup=create_ready_time_keyboard())


async def _finalize_order(message: Message, state: FSMContext, ready_in_min: int):
    user_id = message.from_user.id
    cart = _get_cart(await state.get_data())

    if not cart:
        await state.clear()
        await message.answer("Корзина пустая.", reply_markup=create_client_menu_keyboard())
        return

    try:
        r = await get_redis_client()
        last_order = await r.get(_rate_limit_key(user_id))
        if last_order and time.time() - float(last_order) < RATE_LIMIT_SECONDS:
            await r.aclose()
            await state.clear()
            await message.answer(
                f"⏳ Подождите {RATE_LIMIT_SECONDS} секунд между заказами.",
                reply_markup=create_client_menu_keyboard(),
            )
            return
        await r.setex(_rate_limit_key(user_id), RATE_LIMIT_SECONDS, str(time.time()))
        await r.aclose()
    except Exception:
        pass

    total = _cart_total(cart)
    order_num = str(int(time.time()))[-6:]
    ready_at_str = (get_moscow_time() + timedelta(minutes=max(0, ready_in_min))).strftime("%H:%M")
    ready_line = "как можно скорее" if ready_in_min <= 0 else f"через {ready_in_min} мин (к {ready_at_str} МСК)"

    await set_last_order_snapshot(user_id, {"cart": cart, "total": total, "ts": int(time.time())})

    try:
        r = await get_redis_client()
        await r.incr(STATS_TOTAL_ORDERS)
        await r.incrby(STATS_TOTAL_REVENUE, int(total))
        for drink, qty in cart.items():
            qty_i = int(qty)
            price = int(MENU.get(drink, 0))
            await r.incrby(f"{STATS_DRINK_PREFIX}{drink}", qty_i)
            await r.incrby(f"{STATS_DRINK_REV_PREFIX}{drink}", qty_i * price)
        await r.aclose()
    except Exception:
        pass

    admin_msg = (
        f"🔔 <b>НОВЫЙ ЗАКАЗ #{order_num}</b> | {html.quote(CAFE_NAME)}\n\n"
        f"<a href=\"tg://user?id={user_id}\">{html.quote(message.from_user.username or message.from_user.first_name or 'Клиент')}</a>\n"
        f"<code>{user_id}</code>\n\n"
        + "\n".join(_cart_lines(cart))
        + f"\n\n💰 Итого: <b>{total}₽</b>\n⏱ Готовность: <b>{html.quote(ready_line)}</b>"
    )

    if DEMO_MODE:
        await send_admin_demo_to_user(message.bot, user_id, admin_msg)
    else:
        await send_admin_only(message.bot, admin_msg)

    finish = random.choice(FINISH_VARIANTS).format(name=html.quote(get_user_name(message)))

    await state.clear()
    await message.answer(
        f"🎉 <b>Заказ принят!</b>\n\n{_cart_text(cart)}\n\n⏱ Готовность: {html.quote(ready_line)}\n\n{finish}",
        reply_markup=create_client_menu_keyboard(),
    )


@router.message(StateFilter(OrderStates.waiting_for_ready_time))
async def ready_time(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await _show_cart(message, state)
        return

    if message.text == BTN_READY_NOW:
        await _finalize_order(message, state, 0)
        return

    if message.text == BTN_READY_20:
        await _finalize_order(message, state, 20)
        return

    await message.answer("Выберите кнопкой.", reply_markup=create_ready_time_keyboard())


# ---------------- Booking ----------------
@router.message(F.text == BTN_BOOKING)
async def booking_start(message: Message, state: FSMContext):
    await state.clear()
    if not is_cafe_open():
        await message.answer(get_closed_message(), reply_markup=create_client_menu_keyboard())
        return

    await state.set_state(BookingStates.waiting_for_datetime)
    await message.answer(
        "📅 Введите дату и время в формате <code>15.02 19:00</code>.",
        reply_markup=create_booking_cancel_keyboard(),
    )


@router.message(StateFilter(BookingStates.waiting_for_datetime))
async def booking_datetime(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Ок, бронирование отменено.", reply_markup=create_client_menu_keyboard())
        return

    m = re.match(r"(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})", message.text or "")
    if not m:
        await message.answer("Формат: <code>15.02 19:00</code>.", reply_markup=create_booking_cancel_keyboard())
        return

    day, month, hour, minute = map(int, m.groups())
    year = get_moscow_time().year
    try:
        dt = datetime(year, month, day, hour, minute, tzinfo=MSK_TZ)
    except Exception:
        await message.answer("Не удалось разобрать дату/время.", reply_markup=create_booking_cancel_keyboard())
        return

    await state.update_data(booking_dt=dt.strftime("%d.%m %H:%M"))
    await state.set_state(BookingStates.waiting_for_people)
    await message.answer("На сколько человек? (1–10)", reply_markup=create_booking_people_keyboard())


@router.message(StateFilter(BookingStates.waiting_for_people))
async def booking_people(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Ок, бронирование отменено.", reply_markup=create_client_menu_keyboard())
        return

    try:
        people = int((message.text or "").strip())
        if not (1 <= people <= 10):
            raise ValueError
    except Exception:
        await message.answer("Введите число от 1 до 10.", reply_markup=create_booking_people_keyboard())
        return

    await state.update_data(booking_people=people)
    await state.set_state(BookingStates.waiting_for_comment)
    await message.answer("Комментарий (по желанию) или «-».", reply_markup=create_booking_cancel_keyboard())


@router.message(StateFilter(BookingStates.waiting_for_comment))
async def booking_finish(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Ок, бронирование отменено.", reply_markup=create_client_menu_keyboard())
        return

    data = await state.get_data()
    dt_str = str(data.get("booking_dt") or "")
    people = int(data.get("booking_people") or 0)
    comment = (message.text or "").strip() or "-"

    booking_id = str(int(time.time()))[-6:]
    user_id = message.from_user.id

    await message.answer("✅ Бронь отправлена админу.", reply_markup=create_start_keyboard())

    admin_msg = (
        f"📅 <b>НОВАЯ БРОНЬ #{booking_id}</b> | {html.quote(CAFE_NAME)}\n\n"
        f"<a href=\"tg://user?id={user_id}\">{html.quote(message.from_user.username or message.from_user.first_name or 'Клиент')}</a>\n"
        f"<code>{user_id}</code>\n\n"
        f"🕐 Время: <b>{html.quote(dt_str)}</b>\n"
        f"👥 Гостей: <b>{people}</b>\n"
        f"💬 Комментарий: {html.quote(comment)}"
    )

    if DEMO_MODE:
        await send_admin_demo_to_user(message.bot, user_id, admin_msg)
    else:
        await send_admin_only(message.bot, admin_msg)

    await state.clear()


@router.message(F.from_user.id == ADMIN_ID, StateFilter(None))
async def admin_reply_to_client(message: Message):
    logger.info(
        f"ADMIN CATCHER 2 text={message.text!r} reply={message.reply_to_message is not None}"
    )

    if not message.reply_to_message:
        logger.info("ADMIN CATCHER 2 no reply_to_message")
        return

    if not message.reply_to_message.from_user or not message.reply_to_message.from_user.is_bot:
        logger.info("ADMIN CATCHER 2 reply is not to bot")
        return

    replied_text = message.reply_to_message.text or ""
    logger.info(f"ADMIN CATCHER 2 replied text preview: {replied_text[:150]!r}")

    if "Новая успешная оплата CafebotifySTART" not in replied_text and "CafebotifySTART" not in replied_text:
        logger.info("ADMIN CATCHER 2 not payment notification")
        return

    tgid_match = re.search(r"<code>(\d+)</code>", replied_text)
    if not tgid_match:
        logger.error("ADMIN CATCHER 2 no tgid in notification")
        await message.answer("❌ Не найден ID клиента")
        return

    client_id = int(tgid_match.group(1))
    logger.info(f"ADMIN CATCHER 2 found client_id={client_id}")

    try:
        await message.bot.send_message(
            client_id,
            f"💬 <b>Ответ от поддержки Cafebotify:</b>\n\n{html.quote(message.text or '')}",
            parse_mode="HTML",
        )
        await message.answer(f"✅ Отправлено клиенту <code>{client_id}</code>")
        logger.info(f"ADMIN CATCHER 2 sent to client_id={client_id}")
    except Exception as e:
        logger.error(f"ADMIN CATCHER 2 send error: {e}")
        await message.answer("❌ Ошибка отправки")
        

@router.callback_query(F.data.startswith("paylinks:"))
async def paylinks_send_to_client_callback(callback: CallbackQuery, state: FSMContext):
    logger.info(
        f"PAYLINKS TEST callback data={callback.data!r} "
        f"from_user={callback.from_user.id}"
    )

    await callback.answer("TEST OK")

    if callback.message:
        await callback.message.answer("✅ TEST: callback дошёл до хендлера")

    return
    

@router.message(StateFilter(PaylinksStates.waiting_for_cafe_id))
async def paylinks_cafe_id_input(message: Message, state: FSMContext):
    logger.info(
        f"PAYLINKS DEBUG 4 waiting_for_cafe_id "
        f"text={message.text!r} from={message.from_user.id} chat={message.chat.id}"
    )

    if message.from_user.id not in {ADMIN_ID, SUPERADMIN_ID}:
        logger.info("PAYLINKS DEBUG 4A no access")
        await state.clear()
        return

    text = (message.text or "").strip()
    if not text:
        logger.info("PAYLINKS DEBUG 4B empty text")
        await message.answer("Пришли код кафе, например: <code>cafe_023</code> или <code>23</code>.", parse_mode="HTML")
        return

    cafe_id = text.lower()
    if not cafe_id.startswith("cafe_"):
        digits = "".join(ch for ch in cafe_id if ch.isdigit())
        if not digits:
            logger.info(f"PAYLINKS DEBUG 4C cannot parse cafe_id from text={text!r}")
            await message.answer("Не смог распознать код кафе. Пример: <code>cafe_023</code> или <code>23</code>.", parse_mode="HTML")
            return
        cafe_id = f"cafe_{digits.zfill(3)}"

    logger.info(f"PAYLINKS DEBUG 5 normalized cafe_id={cafe_id}")
    await message.answer(f"✅ Код принят: <code>{cafe_id}</code>", parse_mode="HTML")

    data = await state.get_data()
    draft_id = data.get("draft_id")

    if not draft_id:
        logger.info("PAYLINKS DEBUG 5A no draft_id in state")
        await state.clear()
        await message.answer("❌ draft_id не найден в state. Сценарий нужно запустить заново.")
        return

    try:
        r = await get_redis_client()
        raw = await r.get(_pay_draft_key(draft_id))
        if not raw:
            await r.aclose()
            logger.info(f"PAYLINKS DEBUG 5B draft expired draft_id={draft_id}")
            await state.clear()
            await message.answer("❌ Draft не найден или истёк.")
            return

        payload = json.loads(raw)
        tgid = payload.get("tgid")
        valid_until = int(payload.get("valid_until") or 0)
        product = payload.get("product") or "cafebotify_start_month"
        amount_value = payload.get("amount_value") or ""
        amount_currency = payload.get("amount_currency") or ""
        await r.aclose()
    except Exception as e:
        logger.exception(f"PAYLINKS DEBUG 5C redis read error draft_id={draft_id}")
        await state.clear()
        await message.answer(f"❌ Redis error: {e}")
        return

    if not tgid:
        logger.info(f"PAYLINKS DEBUG 5D no tgid in payload draft_id={draft_id}")
        await state.clear()
        await message.answer("❌ В draft нет Telegram ID клиента.")
        return

    try:
        tgid_int = int(tgid)
    except (TypeError, ValueError):
        logger.info(f"PAYLINKS DEBUG 5E bad tgid={tgid!r}")
        await state.clear()
        await message.answer("❌ Некорректный Telegram ID в draft.")
        return

    tariff_title = "360 дней" if product == "cafebotify_start_year" else "30 дней"
    valid_until_dt = (
        datetime.fromtimestamp(valid_until, tz=MSK_TZ).strftime("%d.%m.%Y %H:%M")
        if valid_until > 0 else "-"
    )

    links_text = build_links_text(str(cafe_id))
    preview_text = (
        "Вот сообщение, которое уйдёт клиенту:\n\n"
        "<b>CafeBotify START</b>\n\n"
        "Ваш бот готов.\n"
        f"Код кафе: <code>{html.quote(str(cafe_id))}</code>\n"
        f"Тариф: <b>{tariff_title}</b>\n"
        f"Доступ до: <b>{valid_until_dt}</b>\n\n"
        "Ссылки:\n"
        f"{links_text}\n\n"
        "Если всё ок — отправь следующим сообщением любой текст или этот же шаблон."
    )

    await state.update_data(
        draft_id=draft_id,
        cafe_id=cafe_id,
        tgid_int=tgid_int,
        valid_until=valid_until,
        product=product,
        amount_value=amount_value,
        amount_currency=amount_currency,
        tariff_title=tariff_title,
        valid_until_dt=valid_until_dt,
        preview_text=preview_text,
    )
    await state.set_state(PaylinksStates.waiting_for_preview_approve)

    logger.info(
        f"PAYLINKS DEBUG 6 state=waiting_for_preview_approve "
        f"draft_id={draft_id} cafe_id={cafe_id} tgid={tgid_int}"
    )

    await message.answer(preview_text, parse_mode="HTML", disable_web_page_preview=True)
    

@router.message(StateFilter(PaylinksStates.waiting_for_preview_approve))
async def paylinks_preview_approve(message: Message, state: FSMContext):
    logger.info(
        f"PAYLINKS DEBUG 7 preview_approve text={message.text!r} from={message.from_user.id}"
    )

    if message.from_user.id != ADMIN_ID and message.from_user.id != SUPERADMIN_ID:
        logger.info("PAYLINKS DEBUG 7A no access")
        await state.clear()
        return

    text = (message.text or "").strip()
    if not text:
        logger.info("PAYLINKS DEBUG 7B empty final text")
        await message.answer("Отправь финальный текст одним сообщением.")
        return

    data = await state.get_data()
    draft_id = data.get("draft_id")
    cafe_id = data.get("cafe_id")
    tgid_int = data.get("tgid_int")

    if not draft_id or not cafe_id or not tgid_int:
        logger.info(
            f"PAYLINKS DEBUG 7C missing data draft_id={draft_id} cafe_id={cafe_id} tgid_int={tgid_int}"
        )
        await state.clear()
        await message.answer("Данные драфта потерялись. Нажми кнопку ещё раз.")
        return

    final_text = text

    try:
        r = await get_redis_client()
        raw = await r.get(_pay_draft_key(draft_id))
        payload = json.loads(raw) if raw else {}
        payload["cafe_id"] = cafe_id
        payload["status"] = "links_sent"
        await r.setex(
            _pay_draft_key(draft_id),
            7 * 86400,
            json.dumps(payload, ensure_ascii=False),
        )
        await r.aclose()
    except Exception as e:
        logger.exception(f"PAYLINKS DEBUG 7D redis update error draft_id={draft_id}")
        await state.clear()
        await message.answer(f"Не смог сохранить cafe_id для драфта: {e}")
        return

    sent_ok = False

    try:
        await message.bot.send_message(
            tgid_int,
            final_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        sent_ok = True
        logger.info(f"PAYLINKS DEBUG 8 sent via demo bot tgid={tgid_int}")
    except Exception as e:
        logger.exception(f"PAYLINKS DEBUG 8A send via demo bot failed draft_id={draft_id} tgid={tgid_int}")
        await message.answer(f"⚠️ Через DEMO-бота отправить не удалось: {e}")

    client_token = (os.getenv("CLIENT_BOT_TOKEN") or "").strip()
    if client_token:
        try:
            client_bot = Bot(token=client_token)
            try:
                await client_bot.send_message(
                    tgid_int,
                    final_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                sent_ok = True
                logger.info(f"PAYLINKS DEBUG 9 sent via client bot tgid={tgid_int}")
            finally:
                await client_bot.session.close()
        except Exception as e:
            logger.exception(f"PAYLINKS DEBUG 9A send via client bot failed draft_id={draft_id} tgid={tgid_int}")
            await message.answer(f"⚠️ Через CLIENT_BOT_TOKEN тоже не удалось: {e}")
    else:
        logger.info("PAYLINKS DEBUG 9B CLIENT_BOT_TOKEN missing")

    await state.clear()
    logger.info("PAYLINKS DEBUG 10 state cleared")

    if not sent_ok:
        await message.answer(
            "❌ Не удалось отправить клиенту.\n"
            "Проверь, писал ли клиент боту и корректен ли tgid."
        )
        return

    await message.answer(
        f"✅ Финальный текст отправлен клиенту.\n"
        f"Draft: <code>{html.quote(str(draft_id))}</code>\n"
        f"Cafe: <code>{html.quote(str(cafe_id))}</code>"
    )
    
@router.message(Command("state"))
async def debug_state_cmd(message: Message, state: FSMContext):
    current_state = await state.get_state()
    data = await state.get_data()
    await message.answer(
        f"state: <code>{html.quote(str(current_state))}</code>\n"
        f"data: <code>{html.quote(str(data))}</code>",
        parse_mode="HTML",
    )


# ---------------- Cafebotify subscriptions helpers ----------------
def _promo_code_for_user(user_id: int) -> str:
    return f"CB{user_id}{(int(time.time()) // 100000) % 10}"


def _in_send_window_msk() -> bool:
    h = get_moscow_time().hour
    return RETURN_SEND_FROM_HOUR <= h < RETURN_SEND_TO_HOUR


async def _get_favorite_drink(user_id: int) -> str:
    key = f"{CUSTOMER_DRINKS_PREFIX}{user_id}"
    try:
        r = await get_redis_client()
        data = await r.hgetall(key)
        await r.aclose()
        best_name, best_cnt = "", -1
        for k, v in data.items():
            try:
                cnt = int(v)
                if cnt > best_cnt:
                    best_cnt = cnt
                    best_name = str(k)
            except Exception:
                continue
        return best_name
    except Exception:
        return ""


async def customer_mark_order(user_id: int, firstname: str, username: str, cart: Dict[str, int], total_sum: int):
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
        pipe.hset(
            customer_key,
            mapping={
                "firstname": firstname or "",
                "username": username or "",
                "last_order_ts": now_ts,
                "last_order_sum": int(total_sum),
                "last_drink": last_drink,
            },
        )
        pipe.hincrby(customer_key, "total_orders", 1)
        pipe.hincrby(customer_key, "total_spent", int(total_sum))
        for drink, qty in cart.items():
            pipe.hincrby(drinks_key, drink, int(qty))
        await pipe.execute()
        await r.aclose()
    except Exception:
        pass


async def smart_return_check_and_send(bot: Bot):
    if not _in_send_window_msk():
        return

    now_ts = int(time.time())
    try:
        r = await get_redis_client()
        ids = await r.smembers(CUSTOMERS_SET_KEY)
        await r.aclose()
        ids = [int(x) for x in ids]
    except Exception:
        ids = []

    for user_id in ids:
        customer_key = f"{CUSTOMER_KEY_PREFIX}{user_id}"
        try:
            r = await get_redis_client()
            profile = await r.hgetall(customer_key)
            await r.aclose()
        except Exception:
            profile = {}

        if not profile or str(profile.get("offers_opt_out", 0)) == "1":
            continue

        try:
            last_order_ts = int(float(profile.get("last_order_ts", 0) or 0))
        except Exception:
            continue

        days_since = (now_ts - last_order_ts) / 86400
        if days_since < RETURN_CYCLE_DAYS:
            continue

        try:
            last_trigger_ts = int(float(profile.get("last_trigger_ts", 0) or 0))
        except Exception:
            last_trigger_ts = 0

        if last_trigger_ts and (now_ts - last_trigger_ts) < RETURN_COOLDOWN_DAYS * 86400:
            continue

        firstname = profile.get("firstname") or ""
        favorite = await _get_favorite_drink(user_id) or profile.get("last_drink") or ""
        promo = _promo_code_for_user(user_id)
        text = (
            f"{html.quote(str(firstname) or 'Друзья')},\n\n"
            f"Скучаете по <b>{html.quote(str(favorite))}</b>? "
            f"Дарим <b>{RETURN_DISCOUNT_PERCENT}% скидку</b> на него по промокоду:\n\n"
            f"<code>{promo}</code>\n\n"
            "Покажите этот код при заказе. Ждём вас!"
        )

        try:
            await bot.send_message(user_id, text)
            try:
                r = await get_redis_client()
                await r.hset(customer_key, "last_trigger_ts", str(now_ts))
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
            logger.error(f"smart_return_loop: {e}")
        await asyncio.sleep(RETURN_CHECK_EVERY_SECONDS)


# ---------------- Subscriptions loop: remind & block ----------------
async def subs_check_and_notify(bot: Bot):
    now_ts = int(time.time())
    try:
        r = await get_redis_client()
        keys = await r.keys("user:*")
        await r.aclose()
    except Exception:
        keys = []

    for key in keys:
        try:
            r = await get_redis_client()
            data = await r.hgetall(key)
            await r.aclose()
        except Exception:
            continue

        try:
            paid_flag = int(data.get("cafebotify_paid", 0))
            valid_until = int(data.get("cafebotify_valid_until", 0))
        except Exception:
            continue

        if not valid_until:
            continue

        user_id_str = key.split("user:")[-1]
        try:
            user_id = int(user_id_str)
        except Exception:
            continue

        days_left = (valid_until - now_ts) / 86400

        # напоминание
        if SUBS_REMIND_DAYS_BEFORE - 0.5 <= days_left <= SUBS_REMIND_DAYS_BEFORE + 0.5:
            pay_url = f"{PAY_LANDING_URL}?tg_id={user_id}&plan=month"
            try:
                await bot.send_message(
                    user_id,
                    "⏰ <b>Скоро заканчивается доступ к CafebotifySTART</b>\n\n"
                    f"Осталось примерно {int(days_left)} дней.\n"
                    f"Продлите по ссылке:\n<a href=\"{html.quote(pay_url)}\">Оплатить ещё месяц</a>",
                    disable_web_page_preview=True,
                )
            except Exception:
                pass

        # блокировка (можно добавить флаг в Redis и проверять его в боте)
        if days_left < 0 and paid_flag == 1:
            try:
                r = await get_redis_client()
                await r.hset(key, mapping={"cafebotify_paid": "0"})
                await r.aclose()
            except Exception:
                pass
            try:
                await bot.send_message(
                    user_id,
                    "🔒 Срок действия CafebotifySTART закончился.\n\n"
                    "Оплатите продление, чтобы снова пользоваться ботом.",
                )
            except Exception:
                pass


async def subs_loop(bot: Bot):
    while True:
        try:
            await subs_check_and_notify(bot)
        except Exception as e:
            logger.error(f"subs_loop: {e}")
        await asyncio.sleep(SUBS_CHECK_EVERY_SECONDS)


# ---------------- ЮKassa HTTP ----------------
async def create_payment(amount: str, description: str, metadata: dict) -> str:
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        raise web.HTTPInternalServerError(text="Yookassa credentials not set")

    url = "https://api.yookassa.ru/v3/payments"
    idem_key = str(uuid.uuid4())

    payload = {
        "amount": {"value": amount, "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": RETURN_URL},
        "description": description,
        "metadata": metadata,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json=payload,
            auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY),
            headers={"Idempotence-Key": idem_key},
            timeout=10,
        )
        if resp.status_code not in (200, 201):
            logger.error(f"Yookassa error {resp.status_code} {resp.text}")
            raise web.HTTPInternalServerError(text="Yookassa error")

        data = resp.json()
        confirmation = data["confirmation"]["confirmation_url"]
        return confirmation


async def pay_month_handler(request: web.Request):
    tgid = (
        request.query.get("tg_id")
        or request.query.get("tgid")
        or request.query.get("admin_id")
    )
    cafe_id = (request.query.get("cafe_id") or "").strip() or None
    
    tgid_int: Optional[int] = None
    if tgid is not None:
        try:
            tgid_int = int(tgid)
        except ValueError:
            tgid_int = None

    amount = os.getenv("CAFEBOTIFY_PRICE", "490.00")
    product = "cafebotify_start_month"
    description = "CafebotifySTART 30 дней"

    metadata = {"product": product}
    if tgid_int is not None:
        metadata["telegram_user_id"] = tgid_int
    if cafe_id:
        metadata["cafe_id"] = cafe_id

    confirmation_url = await create_payment(amount, description, metadata)
    raise web.HTTPFound(confirmation_url)


async def pay_year_handler(request: web.Request):
    tgid = (
        request.query.get("tg_id")
        or request.query.get("tgid")
        or request.query.get("admin_id")
    )
    cafe_id = (request.query.get("cafe_id") or "").strip() or None
    
    tgid_int: Optional[int] = None
    if tgid is not None:
        try:
            tgid_int = int(tgid)
        except ValueError:
            tgid_int = None

    amount = os.getenv("CAFEBOTIFY_PRICE_YEAR", "4900.00")
    product = "cafebotify_start_year"
    description = "CafebotifySTART 360 дней"

    metadata = {"product": product}
    if tgid_int is not None:
        metadata["telegram_user_id"] = tgid_int
    if cafe_id:
        metadata["cafe_id"] = cafe_id

    confirmation_url = await create_payment(amount, description, metadata)
    raise web.HTTPFound(confirmation_url)


async def yookassa_webhook(request: web.Request):
    data = await request.json()
    event = data.get("event")
    obj = data.get("object", {})

    if event != "payment.succeeded":
        return web.json_response({"status": "ignored"})

    metadata = obj.get("metadata", {})
    tgid = metadata.get("telegram_user_id")
    cafe_id = metadata.get("cafe_id")

    payment_id = obj.get("id")
    amount = obj.get("amount", {})
    amount_value = amount.get("value") if isinstance(amount, dict) else None
    amount_currency = amount.get("currency") if isinstance(amount, dict) else None
    payment_status = obj.get("status")

    logger.info(
        f"Yookassa webhook payment_id={payment_id} "
        f"cafe_id={cafe_id} status={payment_status} "
        f"amount={amount_value} {amount_currency} tgid={tgid}"
    )

    if not tgid:
        logger.error(f"Missing telegram_user_id in payment metadata, payment_id={payment_id}")
        return web.json_response({"status": "missing_tgid"})

    try:
        tgid_int = int(tgid)
    except (TypeError, ValueError):
        return web.json_response({"status": "bad_tgid"})

    requested_cafe_id = str(cafe_id).strip() if cafe_id else ""
    cafe_id = requested_cafe_id or None

    # Важно:
    # если cafe_id не пришёл в metadata, считаем это новой оплатой
    # без привязки к конкретному кафе и НЕ пытаемся автоматически
    # восстановить старое кафе по Telegram ID.
    is_new_payment_without_cafe = cafe_id is None

    now_ts = int(time.time())
    product = metadata.get("product") or "cafebotify_start_month"
    period_days = 360 if product == "cafebotify_start_year" else 30
    tariff_title = "360 дней" if product == "cafebotify_start_year" else "30 дней"

    base_ts = now_ts
    if cafe_id:
        sub_key = k_admin_subscription(cafe_id)
        try:
            r = await get_redis_client()
            try:
                raw_until = await r.hget(sub_key, "cafebotify_valid_until")
                current_until = int(raw_until) if raw_until else 0
                if current_until > now_ts:
                    base_ts = current_until
            finally:
                await r.aclose()
        except Exception:
            logger.exception(
                f"yookassa_webhook read current cafe subscription failed "
                f"cafe_id={cafe_id} payment_id={payment_id}"
            )

    valid_until = base_ts + period_days * 86400
    valid_until_dt = datetime.fromtimestamp(valid_until, tz=MSK_TZ).strftime("%d.%m.%Y %H:%M")

    if cafe_id:
        try:
            r = await get_redis_client()
            try:
                eff_admin = await get_effective_admin_id(r, cafe_id)
                await r.hset(
                    k_admin_subscription(cafe_id),
                    mapping={
                        "cafebotify_valid_until": str(valid_until),
                        "cafebotify_paid": "1",
                        "admin_id": str(eff_admin or 0),
                        "last_payment_id": str(payment_id or ""),
                        "last_product": str(product),
                        "last_amount_value": str(amount_value or ""),
                        "last_amount_currency": str(amount_currency or ""),
                        "last_paid_at": str(now_ts),
                    },
                )
            finally:
                await r.aclose()
        except Exception:
            logger.exception(
                f"yookassa_webhook failed to update cafe subscription "
                f"cafe_id={cafe_id} payment_id={payment_id}"
            )
            return web.json_response({"status": "redis_update_failed"})

    draft_id = uuid.uuid4().hex[:12]
    payload = {
        "tgid": tgid_int,
        "cafe_id": cafe_id,
        "payment_id": payment_id,
        "product": product,
        "status": "pending",
        "created_at": now_ts,
        "valid_until": valid_until,
        "amount_value": amount_value,
        "amount_currency": amount_currency,
    }

    try:
        r = await get_redis_client()
        await r.setex(_pay_draft_key(draft_id), 7 * 86400, json.dumps(payload, ensure_ascii=False))
        await r.aclose()
    except Exception as e:
        logger.error(f"yookassa_webhook draft redis error: {e}")
        return web.json_response({"status": "redis_error"})

    cafe_text = (
        f"<code>{html.quote(str(cafe_id))}</code>"
        if cafe_id else
        "<b>не привязан</b>"
    )

    profile_link = f'<a href="tg://user?id={tgid_int}">👤 профиль</a>'

    admin_tail = (
        "Подписка сразу привязана к кафе и обновлена в Redis."
        if cafe_id else
        "Первая оплата принята. Привязка кафе выполняется позже супер-админом."
    )

    admin_text = (
        "💳 <b>Новая успешная оплата CafebotifySTART</b>\n\n"
        f"Кафе: {cafe_text}\n"
        f"👤 <code>{tgid_int}</code> ({profile_link})\n"
        f"Тариф: <b>{tariff_title}</b>\n"
        f"Сумма: <b>{html.quote(str(amount_value or '-'))} {html.quote(str(amount_currency or ''))}</b>\n"
        f"Подписка до: <b>{valid_until_dt}</b>\n"
        f"payment_id: <code>{html.quote(str(payment_id or '-'))}</code>\n"
        f"Draft ID: <code>{draft_id}</code>\n\n"
        f"{admin_tail}"
    )

    admin_kb = None
    if not cafe_id:
        admin_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📨 Отправить ссылки клиенту",
                        callback_data=f"paylinks:{draft_id}",
                    )
                ]
            ]
        )

    demo_bot: Bot = request.app["bot"]

    try:
        await demo_bot.send_message(
            SUPERADMIN_ID,
            admin_text,
            disable_web_page_preview=True,
            parse_mode="HTML",
            reply_markup=admin_kb,
        )
    except Exception:
        logger.exception(
            f"yookassa_webhook superadmin notify error payment_id={payment_id} tgid={tgid}"
        )

    client_token = os.getenv("CLIENT_BOT_TOKEN", "").strip()
    if client_token and cafe_id:
        client_bot = Bot(token=client_token)
        try:
            user_text = (
                "✅ <b>Оплата прошла успешно!</b>\n\n"
                f"Кафе: <code>{html.quote(str(cafe_id))}</code>\n"
                f"Подписка CafebotifySTART на <b>{tariff_title}</b>\n"
                f"активна до <b>{valid_until_dt}</b>."
            )
            await client_bot.send_message(
                tgid_int,
                user_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                )
        except Exception:
            logger.exception(
                f"yookassa_webhook client bot notify error "
                f"payment_id={payment_id} tgid={tgid_int}"
            )
        finally:
            await client_bot.session.close()
    elif not client_token:
        logger.info(
            f"CLIENT_BOT_TOKEN not set, skip client bot notify "
            f"tgid={tgid_int} payment_id={payment_id}"
        )

    if not cafe_id and tgid_int != ADMIN_ID:
        try:
            demo_text = (
                "✅ <b>Оплата прошла успешно</b>\n\n"
                f"Тариф CafebotifySTART активирован на <b>{tariff_title}</b>.\n"
                f"Срок действия: до <b>{valid_until_dt}</b>.\n\n"
                "Следующий шаг — привязка свободного кафе администратором."
            )

            await demo_bot.send_message(
                tgid_int,
                demo_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception(
                f"yookassa_webhook demo bot notify error payment_id={payment_id} tgid={tgid}"
            )

    return web.json_response({"status": "ok"})
    

# ---------------- Команды суперадмина: профиль и оплата ----------------
def _parse_kv_payload(text: str) -> Dict[str, str]:
    """
    /set_profile name=Кафе; phone=+7...; address=...; work_start=9; work_end=21
    """
    payload = text.split(maxsplit=1)
    if len(payload) == 1:
        body = ""
    else:
        body = payload[1]
    result: Dict[str, str] = {}
    for part in body.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip().lower()
        v = v.strip()
        if not k or not v:
            continue
        result[k] = v
    return result


@router.message(Command("set_profile"))
async def set_profile_cmd(message: Message):
    if message.from_user.id != SUPERADMIN_ID:
        return

    params = _parse_kv_payload(message.text or "")
    if not params:
        await message.answer(
            "Формат:\n"
            "/set_profile name=Кофейня; phone=+7...; address=город, улица; work_start=9; work_end=21"
        )
        return

    global CAFE_NAME, CAFE_PHONE, CAFE_ADDRESS, WORK_START, WORK_END

    changes = []

    if "name" in params:
        CAFE_NAME = params["name"]
        changes.append(f"name → <code>{html.quote(CAFE_NAME)}</code>")

    if "phone" in params:
        CAFE_PHONE = params["phone"]
        changes.append(f"phone → <code>{html.quote(CAFE_PHONE)}</code>")

    if "address" in params:
        CAFE_ADDRESS = params["address"]
        changes.append(f"address → <code>{html.quote(CAFE_ADDRESS)}</code>")

    if "work_start" in params:
        try:
            WORK_START = int(params["work_start"])
            changes.append(f"work_start → <code>{WORK_START}</code>")
        except Exception:
            pass

    if "work_end" in params:
        try:
            WORK_END = int(params["work_end"])
            changes.append(f"work_end → <code>{WORK_END}</code>")
        except Exception:
            pass

    # сохранить профиль в CONFIG_PATH (/data/config.json), чтобы переживало рестарт
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

    cafe = data.get("cafe", {})
    cafe["name"] = CAFE_NAME
    cafe["phone"] = CAFE_PHONE
    cafe["address"] = CAFE_ADDRESS
    cafe["work_start"] = WORK_START
    cafe["work_end"] = WORK_END
    data["cafe"] = cafe

    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        await message.answer(
            f"⚠️ Профиль обновлён в памяти, но не удалось сохранить config.json: {e}"
        )
    else:
        await message.answer(
            "✅ Профиль обновлён:\n" + ("\n".join(changes) if changes else "Изменений нет.")
        )


@router.message(Command("checkpaid"))
async def check_paid_cmd(message: Message):
    if message.from_user.id != SUPERADMIN_ID:
        return

    try:
        r = await get_redis_client()
        key = f"user:{message.from_user.id}"
        data = await r.hgetall(key)
        await r.aclose()
    except Exception as e:
        await message.answer(f"Redis error: {e}")
        return

    if not data:
        await message.answer(f"В Redis нет ключа {key}")
        return

    paid = data.get("cafebotify_paid")
    valid_until = data.get("cafebotify_valid_until")
    await message.answer(
        "Redis:\n"
        f"cafebotify_paid = {paid}\n"
        f"cafebotify_valid_until = {valid_until}"
    )


@router.message(Command("set_paid"))
async def set_paid_cmd(message: Message):
    if message.from_user.id != SUPERADMIN_ID:
        return

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer(
            "Формат: /set_paid <tg_id> <YYYY-MM-DD> [month|year]\n"
            "Пример: /set_paid 1471275603 2026-05-01 month"
        )
        return

    tg_id_str, date_str = parts[1], parts[2]
    plan = parts[3] if len(parts) >= 4 else "month"

    try:
        tg_id_int = int(tg_id_str)
    except Exception:
        await message.answer("tg_id должен быть числом.")
        return

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=MSK_TZ)
        base_ts = int(dt.timestamp())
    except Exception:
        await message.answer("Дата в формате YYYY-MM-DD.")
        return

    if plan == "year":
        product = "cafebotify_start_year"
        period_days = 360
    else:
        product = "cafebotify_start_month"
        period_days = 30

    valid_until = base_ts + period_days * 86400

    try:
        r = await get_redis_client()
        await r.hset(
            f"user:{tg_id_int}",
            mapping={
                "cafebotify_paid": "1",
                "cafebotify_paid_at": str(int(time.time())),
                "cafebotify_valid_until": str(valid_until),
                "cafebotify_product": product,
            },
        )
        await r.aclose()
    except Exception as e:
        await message.answer(f"Redis error: {e}")
        return

    await message.answer(
        f"✅ Подписка для <code>{tg_id_int}</code> выставлена до {date_str} ({plan})."
    )


# ---------------- Fallback drink pick ----------------
@router.message(F.text)
async def any_text_message(message: Message, state: FSMContext):
    text = (message.text or "").strip()

    known_buttons = {
        BTN_CLIENT_MENU,
        BTN_OWNER_MENU,
        BTN_TO_START,
        BTN_ABOUT_ASSISTANT,
        BTN_PAY_MONTH,
        BTN_PAY_YEAR,
        BTN_CART,
        BTN_CHECKOUT,
        BTN_BOOKING,
        BTN_CALL,
        BTN_HOURS,
        BTN_STATS,
        BTN_MENU_EDIT,
        BTN_STAFF_GROUP,
        BTN_LINKS,
        BTN_RENEW_SUB,
        BTN_SUBSCRIPTION,
        BTN_ADMIN_HELP,
        BTN_SUPPORT,
        BTN_CONFIRM,
        BTN_CANCEL,
        BTN_CANCEL_ORDER,
        BTN_READY_NOW,
        BTN_READY_20,
        BTN_EDIT_CART,
        BTN_CLEAR_CART,
        BTN_BACK,
        CART_ACT_PLUS,
        CART_ACT_MINUS,
        CART_ACT_DEL,
        CART_ACT_DONE,
        MENU_EDIT_ADD,
        MENU_EDIT_EDIT,
        BTN_TO_CLIENT_MODE,
        MENU_EDIT_DEL,
    }

    if text in known_buttons:
        return

    if text in MENU:
        if not is_cafe_open():
            await message.answer(get_closed_message(), reply_markup=create_client_menu_keyboard())
            return
        await _start_add_item(message, state, text)
        return

    await message.answer(
        "Не понял. Используйте кнопки меню.",
        reply_markup=create_client_menu_keyboard(),
    )


# ---------------- Startup / webhook ----------------
smart_task: Optional[asyncio.Task] = None
subs_task: Optional[asyncio.Task] = None


async def on_startup_bot(bot: Bot):
    global smart_task, subs_task
    await sync_menu_from_redis()
    
    if smart_task is None or smart_task.done():
        smart_task = asyncio.create_task(smart_return_loop(bot))
        
    # Временно отключено: старый subs_loop читает user:* и старые поля
    # cafebotify_paid / cafebotify_valid_until, что конфликтует с новой
    # моделью подписок по cafe:* / admin_subscription.
    # if subs_task is None or subs_task.done():
    #     subs_task = asyncio.create_task(subs_loop(bot))

    try:
        await bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET)
    except Exception as e:
        logger.error(f"Webhook set error: {e}")


async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set")
        return
    if not REDIS_URL:
        logger.error("REDIS_URL not set")
        return

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    storage = RedisStorage.from_url(REDIS_URL)
    dp = Dispatcher(storage=storage)
    dp.include_router(router)
    dp.startup.register(on_startup_bot)

    app = web.Application()
    app["bot"] = bot

    async def healthcheck(request: web.Request):
        return web.json_response({"status": "healthy"})

    app.router.add_get("/", healthcheck)
    app.router.add_get("/healthcheck", healthcheck)
    app.router.add_get("/pay-month", pay_month_handler)
    app.router.add_get("/pay-year", pay_year_handler)
    app.router.add_post("/yookassa_webhook", yookassa_webhook)

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
        handle_in_background=True,
    ).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    async def on_shutdown(a: web.Application):
        global smart_task, subs_task
        try:
            if smart_task and not smart_task.done():
                smart_task.cancel()
        except Exception:
            pass
        try:
            if subs_task and not subs_task.done():
                subs_task.cancel()
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

    app.on_shutdown.append(on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info("Bot started on 0.0.0.0:%s", PORT)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
