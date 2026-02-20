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

import uuid
import httpx  # добавь в requirements.txt: httpx>=0.27.0,<1.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

MSK_TZ = timezone(timedelta(hours=3))
RATE_LIMIT_SECONDS = 60

# --- DEMO mode ---
DEMO_MODE = True  # в клиентской версии можно будет выключить

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

BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "cafebot123")
HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME", "chatbotify-2tjd.onrender.com")
PORT = int(os.getenv("PORT", 10000))

WEBHOOK_PATH = f"/{WEBHOOK_SECRET}/webhook"
WEBHOOK_URL = f"https://{HOSTNAME}{WEBHOOK_PATH}"

# ЮKassa
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
RETURN_URL = os.getenv("YOOKASSA_RETURN_URL", "https://your-domain.ru/pay/success")

# URL лендинга /pay (для кнопки в боте и ссылок из Tilda)
PAY_LANDING_URL = os.getenv("PAY_LANDING_URL", f"https://{HOSTNAME}/pay")

router = Router()


# ---------------- Redis ----------------
async def get_redis_client():
    client = redis.from_url(REDIS_URL, decode_responses=True)
    await client.ping()
    return client


def _rate_limit_key(user_id: int) -> str:
    return f"rate_limit:{user_id}"


def _last_seen_key(user_id: int) -> str:
    return f"{LAST_SEEN_KEY_PREFIX}{user_id}"


def _last_order_key(user_id: int) -> str:
    return f"{LAST_ORDER_KEY_PREFIX}{user_id}"


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
BTN_READY_NOW = "🚶 Сейчас"
BTN_READY_20 = "⏱ Через 20 мин"

CART_ACT_PLUS = "➕ +1"
CART_ACT_MINUS = "➖ -1"
CART_ACT_DEL = "🗑 Удалить"
CART_ACT_DONE = "✅ Готово"

MENU_EDIT_ADD = "➕ Добавить позицию"
MENU_EDIT_EDIT = "✏️ Изменить цену"
MENU_EDIT_DEL = "🗑 Удалить позицию"

# кнопка оплаты
BTN_PAY = "💳 Оплатить CafebotifySTART"


# ---------------- Keyboards ----------------
def create_main_keyboard() -> ReplyKeyboardMarkup:
    kb: list[list[KeyboardButton]] = []
    for drink in MENU.keys():
        kb.append([KeyboardButton(text=drink)])
    kb.append([KeyboardButton(text=BTN_CART), KeyboardButton(text=BTN_CHECKOUT), KeyboardButton(text=BTN_BOOKING)])
    kb.append([KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_CALL), KeyboardButton(text=BTN_HOURS)])
    kb.append([KeyboardButton(text=BTN_MENU_EDIT), KeyboardButton(text=BTN_PAY)])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, is_persistent=True)


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
    kb.append([KeyboardButton(text=BTN_PAY)])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, is_persistent=True)


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
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True, one_time_keyboard=True)


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
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_BACK)]], resize_keyboard=True, one_time_keyboard=True)


def create_pick_menu_item_keyboard() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=k)] for k in MENU.keys()]
    rows.append([KeyboardButton(text=BTN_BACK)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


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
        await message.answer(get_closed_message(), reply_markup=create_main_keyboard())
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
        f"{welcome}\n\n🏪 {get_work_status()}{_address_line()}\n\n"
        "Чтобы добавить в корзину: нажмите напиток → выберите количество.\n"
        "Корзина — «🛒 Корзина».",
        reply_markup=create_main_keyboard(),
    )


@router.message(F.text == BTN_REPEAT_NO)
async def repeat_no(message: Message, state: FSMContext):
    await state.update_data(repeat_offer_snapshot=None)
    await message.answer("Ок.", reply_markup=create_main_keyboard())


@router.message(F.text == BTN_REPEAT_LAST)
async def repeat_last(message: Message, state: FSMContext):
    data = await state.get_data()
    snap = data.get("repeat_offer_snapshot") or await get_last_order_snapshot(message.from_user.id)

    if not snap or not isinstance(snap.get("cart"), dict) or not snap.get("cart"):
        await message.answer("Не нашёл последний заказ.", reply_markup=create_main_keyboard())
        return

    cart = {}
    for k, v in snap["cart"].items():
        try:
            cart[str(k)] = int(v)
        except Exception:
            continue

    filtered = {d: q for d, q in cart.items() if d in MENU and q > 0}
    if not filtered:
        await message.answer("Позиции из прошлого заказа сейчас отсутствуют в меню.", reply_markup=create_main_keyboard())
        return

    await state.update_data(cart=filtered)
    await _show_cart(message, state)


# ---------------- Pay button ----------------
@router.message(F.text == BTN_PAY)
async def pay_button(message: Message):
    user_id = message.from_user.id
    pay_url = f"{PAY_LANDING_URL}?tg_id={user_id}"
    text = (
        "💳 <b>Оплата доступа к CafebotifySTART</b>\n\n"
        "Нажмите на ссылку ниже, чтобы перейти на страницу оплаты.\n"
        "После успешной оплаты доступ будет активирован автоматически."
    )
    await message.answer(
        f"{text}\n\n<a href=\"{html.quote(pay_url)}\">Перейти к оплате</a>",
        reply_markup=create_main_keyboard(),
    )


# ---------------- Info buttons ----------------
@router.message(F.text == BTN_CALL)
async def call_phone(message: Message):
    await message.answer(f"📞 <b>Телефон:</b> <code>{html.quote(CAFE_PHONE)}</code>", reply_markup=create_main_keyboard())


@router.message(F.text == BTN_HOURS)
async def show_hours(message: Message):
    msk_time = get_moscow_time().strftime("%H:%M")
    await message.answer(
        f"🕐 <b>Сейчас:</b> {msk_time} (МСК)\n{get_work_status()}{_address_line()}",
        reply_markup=create_main_keyboard(),
    )


# ---------------- Menu edit entry (DEMO preview for non-admin) ----------------
@router.message(F.text == BTN_MENU_EDIT)
async def menu_edit_entry(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        if DEMO_MODE:
            await message.answer(demo_menu_edit_preview_text(), reply_markup=create_menu_edit_keyboard())
            await message.answer("🔒 Редактирование доступно только администратору.", reply_markup=create_main_keyboard())
        else:
            await message.answer("🔒 Редактирование доступно только администратору.", reply_markup=create_main_keyboard())
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
        await message.answer("Ок.", reply_markup=create_main_keyboard())
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
    await message.answer("✅ Добавлено.", reply_markup=create_main_keyboard())


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
        await message.answer("Позиция не найдена. /start", reply_markup=create_main_keyboard())
        return

    await menu_set_item(name, price)
    await state.clear()
    await message.answer("✅ Цена изменена.", reply_markup=create_main_keyboard())


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
    await message.answer("🗑 Удалено.", reply_markup=create_main_keyboard())


# ---------------- Stats button (DEMO preview for non-admin) ----------------
@router.message(F.text == BTN_STATS)
async def stats_button(message: Message):
    if message.from_user.id != ADMIN_ID:
        if DEMO_MODE:
            await message.answer(demo_stats_preview_text(), reply_markup=create_main_keyboard())
        else:
            await message.answer("📊 Статистика доступна администратору.", reply_markup=create_main_keyboard())
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
        await message.answer(text, reply_markup=create_main_keyboard())
    except Exception:
        await message.answer("❌ Ошибка статистики", reply_markup=create_main_keyboard())


# ---------------- Cart show/clear/cancel ----------------
@router.message(F.text == BTN_CART)
async def cart_button(message: Message, state: FSMContext):
    if not is_cafe_open():
        await message.answer(get_closed_message(), reply_markup=create_main_keyboard())
        return
    await _show_cart(message, state)


@router.message(F.text == BTN_CLEAR_CART)
async def clear_cart(message: Message, state: FSMContext):
    await state.update_data(cart={})
    await _show_cart(message, state)


@router.message(F.text == BTN_CANCEL_ORDER)
async def cancel_order(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Заказ отменён.", reply_markup=create_main_keyboard())


# ---------------- Cart edit ----------------
@router.message(F.text == BTN_EDIT_CART)
async def edit_cart(message: Message, state: FSMContext):
    cart = _get_cart(await state.get_data())
    if not cart:
        await message.answer("Корзина пустая.", reply_markup=create_main_keyboard())
        return
    await state.set_state(OrderStates.cart_edit_pick_item)
    await message.answer("Выберите позицию:", reply_markup=create_cart_pick_item_keyboard(cart))


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


# ---------------- Add item: drink -> quantity ----------------
async def _start_add_item(message: Message, state: FSMContext, drink: str):
    price = MENU.get(drink)
    if price is None:
        await message.answer("Этой позиции уже нет.", reply_markup=create_main_keyboard())
        return

    cart = _get_cart(await state.get_data())
    await state.set_state(OrderStates.waiting_for_quantity)
    await state.update_data(current_drink=drink, cart=cart)

    await message.answer(
        f"{random.choice(CHOICE_VARIANTS)}\n\n🥤 <b>{html.quote(drink)}</b>\n💰 <b>{price}₽</b>\n\nСколько добавить?",
        reply_markup=create_quantity_keyboard(),
    )


@router.message(StateFilter(OrderStates.waiting_for_quantity))
async def process_quantity(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        cart = _get_cart(await state.get_data())
        await message.answer("Ок.", reply_markup=create_cart_keyboard(bool(cart)) if cart else create_main_keyboard())
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
        await message.answer("Ошибка. Нажмите /start.", reply_markup=create_main_keyboard())
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
        await message.answer(get_closed_message(), reply_markup=create_main_keyboard())
        return

    cart = _get_cart(await state.get_data())
    if not cart:
        await message.answer("Корзина пустая.", reply_markup=create_main_keyboard())
        return

    await state.set_state(OrderStates.waiting_for_confirmation)
    await message.answer("✅ <b>Подтвердите заказ</b>\n\n" + _cart_text(cart), reply_markup=create_confirm_keyboard())


@router.message(StateFilter(OrderStates.waiting_for_confirmation))
async def confirm_order(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL_ORDER:
        await state.clear()
        await message.answer("❌ Отменено.", reply_markup=create_main_keyboard())
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
        await message.answer("Корзина пустая.", reply_markup=create_main_keyboard())
        return

    # anti-spam
    try:
        r = await get_redis_client()
        last_order = await r.get(_rate_limit_key(user_id))
        if last_order and time.time() - float(last_order) < RATE_LIMIT_SECONDS:
            await r.aclose()
            await message.answer(f"⏳ Подождите {RATE_LIMIT_SECONDS} секунд между заказами.", reply_markup=create_main_keyboard())
            await state.clear()
            return
        await r.setex(_rate_limit_key(user_id), RATE_LIMIT_SECONDS, str(time.time()))
        await r.aclose()
    except Exception:
        pass

    total = _cart_total(cart)
    order_num = str(int(time.time()))[-6:]
    ready_at_str = (get_moscow_time() + timedelta(minutes=max(0, ready_in_min))).strftime("%H:%M")
    ready_line = "как можно скорее" if ready_in_min <= 0 else f"через {ready_in_min} мин (к {ready_at_str} МСК)"

    # snapshot for "repeat last order"
    await set_last_order_snapshot(user_id, {"cart": cart, "total": total, "ts": int(time.time())})

    # stats (admin)
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
        f"<code>{user_id}</code>\n\n" +
        "\n".join(_cart_lines(cart)) +
        f"\n\n💰 Итого: <b>{total}₽</b>\n⏱ Готовность: <b>{html.quote(ready_line)}</b>"
    )

    await send_admin_only(message.bot, admin_msg)
    await send_admin_demo_to_user(message.bot, user_id, admin_msg)

    finish = random.choice(FINISH_VARIANTS).format(name=html.quote(get_user_name(message)))
    await message.answer(
        f"🎉 <b>Заказ принят!</b>\n\n{_cart_text(cart)}\n\n⏱ Готовность: {html.quote(ready_line)}\n\n{finish}",
        reply_markup=create_main_keyboard(),
    )
    await state.clear()


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
        await message.answer(get_closed_message(), reply_markup=create_main_keyboard())
        return

    await state.set_state(BookingStates.waiting_for_datetime)
    await message.answer(
        "📅 <b>Бронирование</b>\n\nНапишите дату и время: <code>15.02 19:00</code>\nИли «Отмена».",
        reply_markup=create_booking_cancel_keyboard(),
    )


@router.message(StateFilter(BookingStates.waiting_for_datetime))
async def booking_datetime(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
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
    await message.answer("Сколько гостей? (1–10)", reply_markup=create_booking_people_keyboard())


@router.message(StateFilter(BookingStates.waiting_for_people))
async def booking_people(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Ок, отменил.", reply_markup=create_main_keyboard())
        return

    try:
        people = int((message.text or "").strip())
        if not (1 <= people <= 10):
            raise ValueError
    except Exception:
        await message.answer("Нужно число 1–10.", reply_markup=create_booking_people_keyboard())
        return

    await state.update_data(booking_people=people)
    await state.set_state(BookingStates.waiting_for_comment)
    await message.answer("Комментарий (или <code>-</code>):", reply_markup=create_booking_cancel_keyboard())


@router.message(StateFilter(BookingStates.waiting_for_comment))
async def booking_finish(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Ок, отменил.", reply_markup=create_main_keyboard())
        return

    data = await state.get_data()
    dt_str = str(data.get("booking_dt") or "—")
    people = int(data.get("booking_people") or 0)
    comment = (message.text or "").strip() or "-"
    booking_id = str(int(time.time()))[-6:]

    user_id = message.from_user.id
    await message.answer("✅ Заявка на бронь принята!", reply_markup=create_main_keyboard())

    admin_msg = (
        f"📋 <b>НОВАЯ БРОНЬ #{booking_id}</b> | {html.quote(CAFE_NAME)}\n\n"
        f"<a href=\"tg://user?id={user_id}\">{html.quote(message.from_user.username or message.from_user.first_name or 'Клиент')}</a>\n"
        f"<code>{user_id}</code>\n\n"
        f"🗓 {html.quote(dt_str)}\n👥 {people} чел.\n💬 {html.quote(comment)}"
    )

    await send_admin_only(message.bot, admin_msg)
    await send_admin_demo_to_user(message.bot, user_id, admin_msg)
    await state.clear()


# ---------------- Fallback: drink pick ----------------
@router.message(F.text)
async def any_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()

    if text in MENU:
        if not is_cafe_open():
            await message.answer(get_closed_message(), reply_markup=create_main_keyboard())
            return
        await _start_add_item(message, state, text)
        return

    await message.answer("Нажмите напиток или «🛒 Корзина».", reply_markup=create_main_keyboard())


# ---------------- Smart return loop ----------------
def _promo_code(user_id: int) -> str:
    return f"CB{user_id % 10000:04d}{int(time.time()) % 10000:04d}"


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

        if not profile or str(profile.get("offers_opt_out", "0")) == "1":
            continue

        try:
            last_order_ts = int(float(profile.get("last_order_ts", "0") or 0))
        except Exception:
            continue

        days_since = (now_ts - last_order_ts) // 86400
        if days_since < RETURN_CYCLE_DAYS:
            continue

        try:
            last_trigger_ts = int(float(profile.get("last_trigger_ts", "0") or 0))
        except Exception:
            last_trigger_ts = 0

        if last_trigger_ts and (now_ts - last_trigger_ts) < (RETURN_COOLDOWN_DAYS * 86400):
            continue

        first_name = profile.get("first_name") or "друг"
        favorite = await _get_favorite_drink(user_id) or profile.get("last_drink") or "напиток"
        promo = _promo_code(user_id)

        text = (
            f"{html.quote(str(first_name))}, давно не виделись ☕\n\n"
            f"Ваш любимый <b>{html.quote(str(favorite))}</b> сегодня со скидкой <b>{RETURN_DISCOUNT_PERCENT}%</b>.\n"
            f"Промокод: <code>{promo}</code>\n\n"
            "Сделаем заказ? Нажмите /start."
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


# ---------------- Startup/Webhook ----------------
def _promo_code(user_id: int) -> str:
    return f"CB{user_id % 10000:04d}{int(time.time()) % 10000:04d}"


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

        if not profile or str(profile.get("offers_opt_out", "0")) == "1":
            continue

        try:
            last_order_ts = int(float(profile.get("last_order_ts", "0") or 0))
        except Exception:
            continue

        days_since = (now_ts - last_order_ts) // 86400
        if days_since < RETURN_CYCLE_DAYS:
            continue

        try:
            last_trigger_ts = int(float(profile.get("last_trigger_ts", "0") or 0))
        except Exception:
            last_trigger_ts = 0

        if last_trigger_ts and (now_ts - last_trigger_ts) < (RETURN_COOLDOWN_DAYS * 86400):
            continue

        first_name = profile.get("first_name") or "друг"
        favorite = await _get_favorite_drink(user_id) or profile.get("last_drink") or "напиток"
        promo = _promo_code(user_id)

        text = (
            f"{html.quote(str(first_name))}, давно не виделись ☕\n\n"
            f"Ваш любимый <b>{html.quote(str(favorite))}</b> сегодня со скидкой <b>{RETURN_DISCOUNT_PERCENT}%</b>.\n"
            f"Промокод: <code>{promo}</code>\n\n"
            "Сделаем заказ? Нажмите /start."
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


# ---------------- ЮKassa: функции и HTTP-ручки ----------------
async def create_payment(amount: str, description: str, tg_id: int) -> str:
    if not (YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY):
        raise web.HTTPInternalServerError(text="Yookassa credentials not set")
    url = "https://api.yookassa.ru/v3/payments"
    idem_key = str(uuid.uuid4())
    payload = {
        "amount": {"value": amount, "currency": "RUB"},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": RETURN_URL,
        },
        "description": description,
        "metadata": {
            "telegram_user_id": tg_id,
            "product": "cafebotify_start",
        },
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
        logger.error(f"Yookassa error: {resp.status_code} {resp.text}")
        raise web.HTTPInternalServerError(text="Yookassa error")
    data = resp.json()
    return data["confirmation"]["confirmation_url"]


async def pay_handler(request: web.Request):
    tg_id = request.query.get("tg_id")
    if not tg_id:
        raise web.HTTPBadRequest(text="tg_id required")
    try:
        tg_id_int = int(tg_id)
    except ValueError:
        raise web.HTTPBadRequest(text="bad tg_id")

    amount = os.getenv("CAFEBOTIFY_PRICE", "490.00")
    description = f"Подписка CafebotifySTART для Telegram ID {tg_id}"
    confirmation_url = await create_payment(amount, description, tg_id_int)
    raise web.HTTPFound(confirmation_url)


async def yookassa_webhook(request: web.Request):
    data = await request.json()
    event = data.get("event")
    obj = data.get("object", {})

    if event != "payment.succeeded":
        return web.json_response({"status": "ignored"})

    metadata = obj.get("metadata", {})
    tg_id = metadata.get("telegram_user_id")
    if not tg_id:
        return web.json_response({"status": "no_tg_id"})

    # TODO: тут включаешь подписку tg_id в своей БД/Redis
    # пример: r.hset(f"user:{tg_id}", "cafebotify_paid", "1")

    return web.json_response({"status": "ok"})


# ---------------- Startup/Webhook ----------------
_smart_task: Optional[asyncio.Task] = None


async def on_startup(bot: Bot) -> None:
    global _smart_task
    await sync_menu_from_redis()
    if _smart_task is None or _smart_task.done():
        _smart_task = asyncio.create_task(smart_return_loop(bot))

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
    dp.startup.register(on_startup)

    app = web.Application()

    async def healthcheck(request: web.Request):
        return web.json_response({"status": "healthy"})

    app.router.add_get("/", healthcheck)
    app.router.add_get("/pay", pay_handler)
    app.router.add_post("/yookassa/webhook", yookassa_webhook)

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
        handle_in_background=True,
    ).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    async def _on_shutdown(a: web.Application):
        global _smart_task
        try:
            if _smart_task and not _smart_task.done():
                _smart_task.cancel()
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
