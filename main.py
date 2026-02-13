# =========================
# CafeBotify — START v1.0 (DEMO)
# Меню и часы работы из config.json
# Rate-limit: 1 минута, ставится только после оформленного заказа (после выбора времени готовности)
#
# DEMO:
# - После заказа всем тестерам (кто нажал /start) приходят 2 сообщения "как видит админ"
# - После брони всем тестерам (кто нажал /start) приходят 2 сообщения "как видит админ"
# - Кнопка 📊 Статистика видна всем (не-админу показываем демо-отчёт)
# - 🛠 Меню: админ может добавлять/править/удалять позиции (хранение в Redis)
#
# READY TIME:
# - После "Подтвердить" -> выбор: "Сейчас" или "Через 20 мин" или "Отмена"
# =========================

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
        "menu": {
            "☕ Капучино": 250,
            "🥛 Латте": 270,
            "🍵 Чай": 180,
            "⚡ Эспрессо": 200,
        },
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
                    "menu": cafe.get("menu", default_config["menu"]),
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

    return default_config


cafe_config = load_config()

CAFE_NAME = cafe_config["name"]
CAFE_PHONE = cafe_config["phone"]
ADMIN_ID = int(cafe_config["admin_chat_id"])

MENU: Dict[str, int] = dict(cafe_config["menu"])  # синхронизируется с Redis

WORK_START = int(cafe_config["work_start"])
WORK_END = int(cafe_config["work_end"])

BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "cafebot123")
HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME", "chatbotify-2tjd.onrender.com")
PORT = int(os.getenv("PORT", 10000))

WEBHOOK_PATH = f"/{WEBHOOK_SECRET}/webhook"
WEBHOOK_URL = f"https://{HOSTNAME}{WEBHOOK_PATH}"

router = Router()


class OrderStates(StatesGroup):
    waiting_for_quantity = State()
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


def is_cafe_open() -> bool:
    return WORK_START <= get_moscow_time().hour < WORK_END


def get_work_status() -> str:
    msk_hour = get_moscow_time().hour
    if is_cafe_open():
        remaining = max(0, WORK_END - msk_hour)
        return f"🟢 <b>Открыто</b> (ещё {remaining} ч.)"
    return f"🔴 <b>Закрыто</b>\n🕐 Открываемся: {WORK_START}:00 (МСК)"


# ---------- DEMO аудитория (все, кто нажал /start) ----------

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


# ---------- меню: синхронизация с Redis ----------

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
                mapping = {k: str(v) for k, v in MENU.items()}
                await r.hset(MENU_REDIS_KEY, mapping=mapping)
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


# ---------- кнопки ----------

BTN_CALL = "📞 Позвонить"
BTN_HOURS = "⏰ Часы работы"
BTN_STATS = "📊 Статистика"
BTN_BOOKING = "📅 Бронирование"
BTN_MENU_EDIT = "🛠 Меню"

BTN_CANCEL = "🔙 Отмена"
BTN_BACK = "⬅️ Назад"

BTN_CONFIRM = "Подтвердить"
BTN_MENU = "Меню"

# Время готовности (упрощено)
BTN_READY_NOW = "🚶 Сейчас"
BTN_READY_20 = "⏱ Через 20 мин"

MENU_EDIT_ADD = "➕ Добавить позицию"
MENU_EDIT_EDIT = "✏️ Изменить цену"
MENU_EDIT_DEL = "🗑 Удалить позицию"


def create_menu_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text=drink)] for drink in MENU.keys()]
    keyboard.append([KeyboardButton(text=BTN_BOOKING), KeyboardButton(text=BTN_STATS)])
    keyboard.append([KeyboardButton(text=BTN_CALL), KeyboardButton(text=BTN_HOURS), KeyboardButton(text=BTN_MENU_EDIT)])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def create_info_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOKING), KeyboardButton(text=BTN_STATS)],
            [KeyboardButton(text=BTN_CALL), KeyboardButton(text=BTN_HOURS), KeyboardButton(text=BTN_MENU_EDIT)],
        ],
        resize_keyboard=True,
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
        keyboard=[[KeyboardButton(text=BTN_CONFIRM), KeyboardButton(text=BTN_MENU)]],
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


def create_booking_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL), KeyboardButton(text=BTN_MENU)]],
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


# ---------- тёплые тексты ----------

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
    "Вот это да! Любители хорошего кофе тебя поймут.",
    "Смело! Такой выбор обычно делают настоящие ценители.",
    "{name}, ты знаешь толк в напитках.",
    "Звучит вкусно — уже представляю аромат.",
]

FINISH_VARIANTS = [
    "Спасибо за заказ, {name}! Буду рад увидеть тебя снова.",
    "Рад был помочь с выбором, {name}. Заглядывай ещё — всегда ждём.",
    "Отличный заказ, {name}! Надеюсь, это сделает день чуточку лучше.",
    "Спасибо, что выбрал именно нас, {name}. До следующей кофейной паузы!",
    "Заказ готовим с заботой. Возвращайся, когда захочется повторить.",
]


def get_closed_message() -> str:
    menu_text = " • ".join([f"<b>{html.quote(drink)}</b> {price}₽" for drink, price in MENU.items()])
    return (
        f"🔒 <b>{html.quote(CAFE_NAME)} сейчас закрыто!</b>\n\n"
        f"⏰ {get_work_status()}\n\n"
        f"☕ <b>Наше меню:</b>\n{menu_text}\n\n"
        f"📞 <b>Связаться:</b>\n<code>{html.quote(CAFE_PHONE)}</code>\n\n"
        f"✨ <i>До скорой встречи!</i>"
    )


def get_user_name(message: Message) -> str:
    if message.from_user is None:
        return "друг"
    return message.from_user.first_name or "друг"


def _is_reserved_button(text: str) -> bool:
    reserved = {
        BTN_CALL, BTN_HOURS, BTN_STATS, BTN_BOOKING, BTN_MENU_EDIT,
        BTN_CANCEL, BTN_BACK, BTN_CONFIRM, BTN_MENU,
        BTN_READY_NOW, BTN_READY_20,
        MENU_EDIT_ADD, MENU_EDIT_EDIT, MENU_EDIT_DEL,
    }
    return text in reserved


# -------------------------
# Статистика
# -------------------------

def _build_demo_stats() -> tuple[int, Dict[str, int]]:
    drinks = list(MENU.keys())
    base = [61, 39, 17, 10, 6, 4, 3, 2, 1]
    by_drink: Dict[str, int] = {}
    for i, d in enumerate(drinks):
        by_drink[d] = base[i] if i < len(base) else 1
    total = sum(by_drink.values())
    return total, by_drink


async def _send_real_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        r_client = await get_redis_client()
        total_orders = int(await r_client.get("stats:total_orders") or 0)

        stats_text = "📊 <b>Статистика заказов</b>\n\n"
        stats_text += f"Всего заказов: <b>{total_orders}</b>\n\n"

        for drink in MENU.keys():
            count = int(await r_client.get(f"stats:drink:{drink}") or 0)
            if count > 0:
                stats_text += f"{html.quote(drink)}: {count}\n"

        await r_client.aclose()
        await message.answer(stats_text, reply_markup=create_menu_keyboard())
    except Exception:
        await message.answer("❌ Ошибка статистики", reply_markup=create_menu_keyboard())


async def _send_demo_stats(message: Message):
    total, by_drink = _build_demo_stats()
    stats_text = (
        "📊 <b>Статистика заказов (DEMO)</b>\n\n"
        "Пример того, как будет выглядеть отчёт для владельца/администратора.\n\n"
        f"Всего заказов: <b>{total}</b>\n\n"
    )
    for drink in MENU.keys():
        stats_text += f"{html.quote(drink)}: {by_drink.get(drink, 0)}\n"
    await message.answer(stats_text, reply_markup=create_menu_keyboard())


# -------------------------
# Сообщения "как видит админ"
# -------------------------

def _format_ready_line(ready_in_min: int) -> str:
    if ready_in_min <= 0:
        return "⏱ Готовность: <b>как можно скорее</b>"
    ready_at = (get_moscow_time() + timedelta(minutes=ready_in_min)).strftime("%H:%M")
    return f"⏱ Готовность: <b>через {ready_in_min} мин</b> (к {ready_at} МСК)"


def build_admin_order_messages(
    *,
    order_num: str,
    user_id: int,
    user_name: str,
    drink: str,
    quantity: int,
    total: int,
    ready_in_min: int,
) -> tuple[str, str]:
    safe_user_name = html.quote(user_name)
    safe_drink = html.quote(drink)
    user_link = f'<a href="tg://user?id={user_id}">{safe_user_name}</a>'

    msg1 = (
        f"🔔 <b>НОВЫЙ ЗАКАЗ #{order_num}</b> | {html.quote(CAFE_NAME)}\n\n"
        f"{user_link}\n"
        f"<code>{user_id}</code>\n\n"
        f"{safe_drink}\n"
        f"{quantity} порций\n"
        f"<b>{total} ₽</b>\n"
        f"{_format_ready_line(ready_in_min)}\n\n"
        f"Нажми на имя, чтобы открыть чат и ответить клиенту."
    )

    msg2 = (
        "ℹ️ <b>ПРИМЕР ПОДТВЕРЖДЁННОГО ЗАКАЗА (КАК ВИДИТ АДМИН)</b>\n\n"
        "В рабочем режиме бот будет присылать вам каждое подтверждение заказа в таком виде.\n\n"
        "Нажмите на имя клиента, чтобы открыть чат и уточнить детали."
    )
    return msg1, msg2


def build_admin_booking_messages(
    *,
    booking_id: str,
    user_id: int,
    user_name: str,
    dt_str: str,
    people: int,
    comment: str,
) -> tuple[str, str]:
    safe_user_name = html.quote(user_name)
    safe_dt = html.quote(dt_str)
    safe_comment = html.quote(comment)
    user_link = f'<a href="tg://user?id={user_id}">{safe_user_name}</a>'

    msg1 = (
        f"📋 <b>НОВАЯ ЗАЯВКА НА БРОНЬ #{booking_id}</b> | {html.quote(CAFE_NAME)}\n\n"
        f"{user_link}\n"
        f"<code>{user_id}</code>\n\n"
        f"🗓 {safe_dt}\n"
        f"👥 {people} чел.\n"
        f"💬 {safe_comment}\n\n"
        f"Нажми на имя, чтобы открыть чат и ответить клиенту."
    )

    msg2 = (
        "ℹ️ <b>ПРИМЕР ЗАЯВКИ НА БРОНЬ (КАК ВИДИТ АДМИН)</b>\n\n"
        "Так бот будет присылать владельцу/администратору заявки на бронирование.\n"
        "Админ подтверждает/уточняет бронь уже напрямую в Telegram.\n\n"
        "Нажмите на имя клиента, чтобы открыть чат и уточнить детали."
    )
    return msg1, msg2


# -------------------------
# /start
# -------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await register_demo_subscriber(message.from_user.id)

    user_id = message.from_user.id
    name = get_user_name(message)
    msk_time = get_moscow_time().strftime("%H:%M")
    logger.info(f"👤 /start от {user_id} | MSK: {msk_time}")

    welcome = random.choice(WELCOME_VARIANTS).format(name=name)

    if is_cafe_open():
        await message.answer(
            f"{welcome}\n\n"
            f"🕐 <i>Московское время: {msk_time}</i>\n"
            f"🏪 {get_work_status()}\n\n"
            f"☕ <b>Выберите напиток:</b>",
            reply_markup=create_menu_keyboard(),
        )
    else:
        await message.answer(get_closed_message(), reply_markup=create_info_keyboard())


# -------------------------
# Общие кнопки (обработчики должны быть выше общего F.text)
# -------------------------

@router.message(F.text == BTN_STATS)
async def stats_button(message: Message):
    await register_demo_subscriber(message.from_user.id)
    if message.from_user.id == ADMIN_ID:
        await _send_real_stats(message)
    else:
        await _send_demo_stats(message)


@router.message(Command("stats"))
async def stats_command(message: Message):
    await register_demo_subscriber(message.from_user.id)
    if message.from_user.id == ADMIN_ID:
        await _send_real_stats(message)
    else:
        await _send_demo_stats(message)


@router.message(F.text == BTN_CALL)
async def call_phone(message: Message):
    await register_demo_subscriber(message.from_user.id)
    name = get_user_name(message)

    if is_cafe_open():
        text = (
            f"{name}, буду рад помочь!\n\n"
            f"📞 <b>Телефон {html.quote(CAFE_NAME)}:</b>\n<code>{html.quote(CAFE_PHONE)}</code>\n\n"
            "Если удобнее — можешь просто выбрать напиток в меню, я всё оформлю здесь."
        )
        await message.answer(text, reply_markup=create_menu_keyboard())
    else:
        text = (
            f"{name}, сейчас мы закрыты, но я всё равно подскажу.\n\n"
            f"📞 <b>Телефон {html.quote(CAFE_NAME)}:</b>\n<code>{html.quote(CAFE_PHONE)}</code>\n\n"
            f"⏰ {get_work_status()}\n\n"
            "Хочешь — посмотри меню, а заказ оформим, как только откроемся."
        )
        await message.answer(text, reply_markup=create_info_keyboard())


@router.message(F.text == BTN_HOURS)
async def show_hours(message: Message):
    await register_demo_subscriber(message.from_user.id)
    name = get_user_name(message)
    msk_time = get_moscow_time().strftime("%H:%M")
    text = (
        f"{name}, вот актуальная информация:\n\n"
        f"🕐 <b>Сейчас:</b> {msk_time} (МСК)\n"
        f"🏪 {get_work_status()}\n\n"
        f"📞 Телефон: <code>{html.quote(CAFE_PHONE)}</code>"
    )
    await message.answer(text, reply_markup=create_menu_keyboard() if is_cafe_open() else create_info_keyboard())


# -------------------------
# Заказ: FSM
# -------------------------

async def _start_order(message: Message, state: FSMContext, drink: str):
    if not is_cafe_open():
        await message.answer(get_closed_message(), reply_markup=create_info_keyboard())
        return

    price = MENU.get(drink)
    if price is None:
        await message.answer("Этого напитка уже нет в меню. Нажмите /start.", reply_markup=create_menu_keyboard())
        return

    await state.set_state(OrderStates.waiting_for_quantity)
    await state.set_data({"drink": drink, "price": price})

    choice_text = random.choice(CHOICE_VARIANTS).format(name=get_user_name(message))
    await message.answer(
        f"{choice_text}\n\n"
        f"🥤 <b>{html.quote(drink)}</b>\n💰 <b>{price} ₽</b>\n\n📝 <b>Сколько порций?</b>",
        reply_markup=create_quantity_keyboard(),
    )


@router.message(StateFilter(OrderStates.waiting_for_quantity))
async def process_quantity(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)

    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer(
            "❌ Заказ отменён",
            reply_markup=create_menu_keyboard() if is_cafe_open() else create_info_keyboard(),
        )
        return

    try:
        quantity = int((message.text or "")[0])
        if 1 <= quantity <= 5:
            data = await state.get_data()
            drink, price = data["drink"], data["price"]
            total = price * quantity

            await state.set_state(OrderStates.waiting_for_confirmation)
            await state.update_data(quantity=quantity, total=total)

            await message.answer(
                f"🥤 <b>{html.quote(drink)}</b> × {quantity}\n💰 Итого: <b>{total} ₽</b>\n\n✅ Правильно?",
                reply_markup=create_confirm_keyboard(),
            )
        else:
            await message.answer("❌ Выберите от 1 до 5", reply_markup=create_quantity_keyboard())
    except Exception:
        await message.answer("❌ Нажмите на кнопку", reply_markup=create_quantity_keyboard())


@router.message(StateFilter(OrderStates.waiting_for_confirmation))
async def process_confirmation(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)

    if message.text == BTN_MENU:
        await state.clear()
        await message.answer("☕ Меню:", reply_markup=create_menu_keyboard())
        return

    if message.text != BTN_CONFIRM:
        await message.answer("❌ Нажмите кнопку", reply_markup=create_confirm_keyboard())
        return

    await state.set_state(OrderStates.waiting_for_ready_time)
    await message.answer(
        "⏱ <b>Когда удобно забрать заказ?</b>\n\nВыберите вариант:",
        reply_markup=create_ready_time_keyboard(),
    )


async def _finalize_order(message: Message, state: FSMContext, ready_in_min: int):
    user_id = message.from_user.id

    # rate-limit после финализации
    try:
        r_client = await get_redis_client()
        last_order = await r_client.get(_rate_limit_key(user_id))
        if last_order and time.time() - float(last_order) < RATE_LIMIT_SECONDS:
            await message.answer(
                f"⏳ Дай мне минутку: новый заказ можно оформить через {RATE_LIMIT_SECONDS} секунд после предыдущего.",
                reply_markup=create_menu_keyboard(),
            )
            await r_client.aclose()
            await state.clear()
            return
        await r_client.setex(_rate_limit_key(user_id), RATE_LIMIT_SECONDS, time.time())
        await r_client.aclose()
    except Exception:
        pass

    data = await state.get_data()
    drink, quantity, total = data["drink"], data["quantity"], data["total"]

    order_id = f"order:{int(time.time())}:{user_id}"
    order_num = order_id.split(":")[-1]

    user_name = message.from_user.username or message.from_user.first_name or "Клиент"
    ready_at_dt = get_moscow_time() + timedelta(minutes=max(0, ready_in_min))
    ready_at_str = ready_at_dt.strftime("%H:%M")

    # сохраняем заказ + статистику
    try:
        r_client = await get_redis_client()
        await r_client.hset(
            order_id,
            mapping={
                "user_id": user_id,
                "username": user_name,
                "drink": drink,
                "quantity": quantity,
                "total": total,
                "ready_in_min": ready_in_min,
                "ready_at_msk": ready_at_str,
                "timestamp": datetime.now().isoformat(),
            },
        )
        await r_client.expire(order_id, 86400)
        await r_client.incr("stats:total_orders")
        await r_client.incr(f"stats:drink:{drink}")
        await r_client.aclose()
    except Exception:
        pass

    # DEMO: "как видит админ"
    msg1, msg2 = build_admin_order_messages(
        order_num=order_num,
        user_id=user_id,
        user_name=user_name,
        drink=drink,
        quantity=quantity,
        total=total,
        ready_in_min=ready_in_min,
    )
    await send_to_demo_audience(message.bot, msg1, include_admin=True)
    await send_to_demo_audience(message.bot, msg2, include_admin=True)

    finish_text = random.choice(FINISH_VARIANTS).format(name=get_user_name(message))
    if ready_in_min <= 0:
        ready_user_line = "⏱ Готовность: как можно скорее"
    else:
        ready_user_line = f"⏱ Готовность: через {ready_in_min} мин (к {ready_at_str} МСК)"

    await message.answer(
        f"🎉 <b>Заказ #{order_num} принят!</b>\n\n"
        f"🥤 {html.quote(drink)} × {quantity}\n"
        f"💰 {total}₽\n"
        f"{ready_user_line}\n\n"
        f"{finish_text}",
        reply_markup=create_menu_keyboard(),
    )
    await state.clear()


@router.message(StateFilter(OrderStates.waiting_for_ready_time))
async def process_ready_time(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)

    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Ок, заказ отменён.", reply_markup=create_menu_keyboard())
        return

    if message.text == BTN_READY_NOW:
        await _finalize_order(message, state, 0)
        return

    if message.text == BTN_READY_20:
        await _finalize_order(message, state, 20)
        return

    await message.answer("Выберите вариант кнопкой.", reply_markup=create_ready_time_keyboard())


# -------------------------
# Бронирование
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
        "📅 <b>Бронирование столика</b>\n\n"
        "Напишите дату и время в формате:\n<code>15.02 19:00</code>\n\n"
        "Или нажмите «Отмена».",
        reply_markup=create_booking_cancel_keyboard(),
    )


@router.message(StateFilter(BookingStates.waiting_for_datetime))
async def booking_datetime(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)

    if message.text in {BTN_CANCEL, BTN_MENU}:
        await state.clear()
        await message.answer("Ок, бронирование отменено.", reply_markup=create_menu_keyboard())
        return

    if _is_reserved_button(message.text or "") or (message.text in MENU):
        await message.answer(
            "Для бронирования напишите дату и время как в примере:\n<code>15.02 19:00</code>",
            reply_markup=create_booking_cancel_keyboard(),
        )
        return

    m = re.match(r"^\s*(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})\s*$", message.text or "")
    if not m:
        await message.answer("Не понял формат.\nНапишите так: <code>15.02 19:00</code>", reply_markup=create_booking_cancel_keyboard())
        return

    day, month, hour, minute = map(int, m.groups())
    year = get_moscow_time().year
    try:
        dt = datetime(year, month, day, hour, minute, tzinfo=MSK_TZ)
    except Exception:
        await message.answer("Похоже, дата/время некорректны. Попробуйте ещё раз.", reply_markup=create_booking_cancel_keyboard())
        return

    dt_str = dt.strftime("%d.%m %H:%M")
    await state.update_data(booking_dt=dt_str)
    await state.set_state(BookingStates.waiting_for_people)

    await message.answer(
        f"Отлично! 🗓 <b>{dt_str}</b>\n\nСколько гостей будет?",
        reply_markup=create_booking_people_keyboard(),
    )


@router.message(StateFilter(BookingStates.waiting_for_people))
async def booking_people(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)

    if message.text in {BTN_CANCEL, BTN_MENU}:
        await state.clear()
        await message.answer("Ок, бронирование отменено.", reply_markup=create_menu_keyboard())
        return

    if _is_reserved_button(message.text or "") or (message.text in MENU):
        await message.answer("Введите число гостей (1–10) или нажмите «Отмена».", reply_markup=create_booking_people_keyboard())
        return

    try:
        people = int((message.text or "").strip())
        if not (1 <= people <= 10):
            raise ValueError
    except Exception:
        await message.answer("Нужно число от 1 до 10.", reply_markup=create_booking_people_keyboard())
        return

    await state.update_data(booking_people=people)
    await state.set_state(BookingStates.waiting_for_comment)

    await message.answer(
        "Короткий комментарий (имя/пожелания/контакт) — или отправьте <code>-</code>, если без комментария.",
        reply_markup=create_booking_cancel_keyboard(),
    )


@router.message(StateFilter(BookingStates.waiting_for_comment))
async def booking_finish(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)

    if message.text in {BTN_CANCEL, BTN_MENU}:
        await state.clear()
        await message.answer("Ок, бронирование отменено.", reply_markup=create_menu_keyboard())
        return

    if _is_reserved_button(message.text or "") or (message.text in MENU):
        await message.answer(
            "Пожалуйста, напишите комментарий (или <code>-</code>), либо нажмите «Отмена».",
            reply_markup=create_booking_cancel_keyboard(),
        )
        return

    data = await state.get_data()
    dt_str = data.get("booking_dt", "—")
    people = int(data.get("booking_people", 0) or 0)

    comment = (message.text or "").strip() or "-"
    comment_out = "—" if comment == "-" else comment

    booking_id = f"{int(time.time())}{message.from_user.id}"

    await message.answer(
        "✅ <b>Заявка на бронь принята!</b>\n\n"
        "Мы свяжемся с Вами для подтверждения в течение 15 минут.\n\n"
        "Если планы изменились — просто напишите сюда.",
        reply_markup=create_menu_keyboard(),
    )

    user_name = message.from_user.username or message.from_user.first_name or "Клиент"
    user_id = message.from_user.id

    msg1, msg2 = build_admin_booking_messages(
        booking_id=str(booking_id),
        user_id=user_id,
        user_name=user_name,
        dt_str=dt_str,
        people=people,
        comment=comment_out,
    )
    await send_to_demo_audience(message.bot, msg1, include_admin=True)
    await send_to_demo_audience(message.bot, msg2, include_admin=True)

    await state.clear()


# -------------------------
# Меню-редактор (админ)
# -------------------------

def _menu_as_text() -> str:
    if not MENU:
        return "Меню пока пустое."
    lines = []
    for k, v in MENU.items():
        lines.append(f"• <b>{html.quote(k)}</b> — {v}₽")
    return "\n".join(lines)


@router.message(F.text == BTN_MENU_EDIT)
async def menu_edit_entry(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)

    if message.from_user.id != ADMIN_ID:
        await message.answer(
            "🛠 <b>Управление меню</b>\n\n"
            "В клиентской версии это доступно владельцу/админу.\n"
            "В демо ниже — пример текущего меню:\n\n"
            f"{_menu_as_text()}",
            reply_markup=create_menu_keyboard(),
        )
        return

    await state.clear()
    await state.set_state(MenuEditStates.waiting_for_action)
    await message.answer(
        "🛠 <b>Управление меню</b>\n\n"
        f"{_menu_as_text()}\n\n"
        "Выберите действие:",
        reply_markup=create_menu_edit_keyboard(),
    )


@router.message(StateFilter(MenuEditStates.waiting_for_action))
async def menu_edit_choose_action(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    if message.text == BTN_BACK:
        await state.clear()
        await message.answer("Ок.", reply_markup=create_menu_keyboard())
        return

    if message.text == MENU_EDIT_ADD:
        await state.set_state(MenuEditStates.waiting_for_add_name)
        await message.answer(
            "Введите название новой позиции (например: <code>🥐 Круассан</code>)",
            reply_markup=create_menu_edit_cancel_keyboard(),
        )
        return

    if message.text == MENU_EDIT_EDIT:
        await state.set_state(MenuEditStates.waiting_for_edit_name)
        await message.answer(
            "Введите точное название позиции, цену которой нужно изменить.\n\n"
            f"{_menu_as_text()}",
            reply_markup=create_menu_edit_cancel_keyboard(),
        )
        return

    if message.text == MENU_EDIT_DEL:
        await state.set_state(MenuEditStates.waiting_for_remove_name)
        await message.answer(
            "Введите точное название позиции, которую нужно удалить.\n\n"
            f"{_menu_as_text()}",
            reply_markup=create_menu_edit_cancel_keyboard(),
        )
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
        await message.answer("Введите корректное название позиции.", reply_markup=create_menu_edit_cancel_keyboard())
        return

    await state.update_data(add_name=name)
    await state.set_state(MenuEditStates.waiting_for_add_price)
    await message.answer("Введите цену числом (например: <code>250</code>)", reply_markup=create_menu_edit_cancel_keyboard())


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
        await message.answer("Цена должна быть числом (например 250).", reply_markup=create_menu_edit_cancel_keyboard())
        return

    data = await state.get_data()
    name = (data.get("add_name") or "").strip()
    if not name:
        await state.clear()
        await message.answer("Ошибка состояния. Начните заново.", reply_markup=create_menu_keyboard())
        return

    await menu_set_item(name, price)
    await state.clear()
    await message.answer("✅ Позиция добавлена.\n\n" + _menu_as_text(), reply_markup=create_menu_keyboard())


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
        await message.answer("Не нашёл такую позицию. Введите точное название из списка.", reply_markup=create_menu_edit_cancel_keyboard())
        return

    await state.update_data(edit_name=name)
    await state.set_state(MenuEditStates.waiting_for_edit_price)
    await message.answer(
        f"Введите новую цену для <b>{html.quote(name)}</b> (числом):",
        reply_markup=create_menu_edit_cancel_keyboard(),
    )


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
        await message.answer("Цена должна быть числом (например 270).", reply_markup=create_menu_edit_cancel_keyboard())
        return

    data = await state.get_data()
    name = (data.get("edit_name") or "").strip()
    if name not in MENU:
        await state.clear()
        await message.answer("Позиция не найдена. Начните заново.", reply_markup=create_menu_keyboard())
        return

    await menu_set_item(name, price)
    await state.clear()
    await message.answer("✅ Цена изменена.\n\n" + _menu_as_text(), reply_markup=create_menu_keyboard())


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
        await message.answer("Не нашёл такую позицию. Введите точное название из списка.", reply_markup=create_menu_edit_cancel_keyboard())
        return

    await menu_delete_item(name)
    await state.clear()
    await message.answer("🗑 Позиция удалена.\n\n" + _menu_as_text(), reply_markup=create_menu_keyboard())


# -------------------------
# Общий обработчик текста (выбор напитка)
# -------------------------

@router.message(StateFilter(None), F.text)
async def any_text_outside_states(message: Message, state: FSMContext):
    await register_demo_subscriber(message.from_user.id)
    text = (message.text or "").strip()

    if text in MENU:
        await _start_order(message, state, text)
        return

    if _is_reserved_button(text):
        return

    await message.answer("Выбери напиток кнопкой в меню или нажми «Бронирование».", reply_markup=create_menu_keyboard())


# -------------------------
# Help
# -------------------------

@router.message(Command("help"))
async def help_command(message: Message):
    await register_demo_subscriber(message.from_user.id)
    text = (
        "Этот бот — демо-ассистент для кофейни.\n\n"
        "Что он умеет:\n"
        "• Меню и быстрые заказы\n"
        "• Время готовности (сейчас / через 20 минут)\n"
        "• Заявки на бронирование\n"
        "• Статистика (в демо — пример)\n\n"
        "Связаться: @denvyd"
    )
    await message.answer(text, reply_markup=create_menu_keyboard())


# -------------------------
# Startup / Webhook
# -------------------------

async def on_startup(bot: Bot) -> None:
    logger.info("🚀 Запуск бота (START v1.0 DEMO)...")
    logger.info(f"☕ Кафе: {CAFE_NAME}")
    logger.info(f"⏰ Часы работы: {WORK_START}:00–{WORK_END}:00 (МСК)")
    logger.info(f"⏳ Rate-limit: {RATE_LIMIT_SECONDS} сек.")
    logger.info(f"🔗 Webhook (target): {WEBHOOK_URL}")

    try:
        r_test = redis.from_url(REDIS_URL)
        await r_test.ping()
        await r_test.aclose()
        logger.info("✅ Redis подключён")
    except Exception as e:
        logger.error(f"❌ Redis: {e}")

    await sync_menu_from_redis()

    try:
        current_webhook = await bot.get_webhook_info()
        logger.info(f"Текущий webhook: {current_webhook.url}")

        await bot.set_webhook(
            WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET,
        )
        logger.info("✅ Webhook (re)set выполнен")
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
        logger.info("🛑 Бот остановлен")

    app.on_shutdown.append(_on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)

    logger.info(f"🌐 Сервер запущен на 0.0.0.0:{PORT}")
    await site.start()

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
