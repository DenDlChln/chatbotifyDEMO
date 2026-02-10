<<<<<<< HEAD
=======
# =========================
# CafeBotify — START v1.0 (DEMO)
# Меню и часы работы из config.json
# Rate-limit: 1 минута, ставится только после подтверждённого заказа
# NEW:
# 1) Админ отвечает, нажав на имя клиента (tg://user?id=...)
# 2) Телефон кафе убран из уведомления админу
# 3) Приветствие /start — 5 тёплых вариантов (рандом)
# 4) После выбора напитка — 8 тёплых вариантов (рандом)
# 5) Завершение заказа — 5 тёплых вариантов (рандом)
# =========================

>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
import os
import json
import logging
import asyncio
import time
import random
from datetime import datetime, timezone, timedelta
<<<<<<< HEAD
from typing import Dict, Any, Optional
=======
from typing import Dict, Any, Optional, Tuple
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851

import redis.asyncio as redis
from aiohttp import web

from aiogram import Bot, Dispatcher, F, Router
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
<<<<<<< HEAD
    BotCommand,
    ChatMemberUpdated,
    ErrorEvent,
)
from aiogram.filters import CommandStart, Command, StateFilter, CommandObject
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from aiogram.utils.deep_linking import create_start_link, create_startgroup_link

from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter
from aiogram.filters import IS_NOT_MEMBER, IS_MEMBER


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

=======
)
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
MSK_TZ = timezone(timedelta(hours=3))
RATE_LIMIT_SECONDS = 60


<<<<<<< HEAD
def get_moscow_time() -> datetime:
    return datetime.now(MSK_TZ)


# -------------------------
# CONFIG (multi-cafe) + DIAGNOSTIC
# -------------------------

def load_config_file() -> Dict[str, Any]:
    path = os.getenv("CONFIG_PATH", "config.json")

    logger.info("=== IMPORT MARK: MULTI-CAFE DIAG LOADED ===")
    logger.info(f"CONFIG_PATH={path}")
    try:
        logger.info(f"CWD={os.getcwd()}")
    except Exception as e:
        logger.info(f"CWD error: {e}")

    try:
        logger.info("DIR=" + ", ".join(os.listdir(".")))
    except Exception as e:
        logger.info(f"DIR list error: {e}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            cafes_count = len(data.get("cafes", [])) if isinstance(data, dict) else "n/a"
            logger.info(f"CONFIG loaded cafes={cafes_count}")
            if not isinstance(data, dict):
                raise ValueError("config root must be object/dict")
            return data
    except FileNotFoundError as e:
        logger.error(f"CONFIG not found: {e}")
    except json.JSONDecodeError as e:
        logger.error(f"CONFIG JSON invalid: {e}")
    except Exception as e:
        logger.error(f"CONFIG load error: {e}")

    return {}


CONFIG = load_config_file()
CAFES = CONFIG.get("cafes", [])
if not isinstance(CAFES, list):
    CAFES = []

DEFAULT_CAFE = {
    "id": "default_cafe",
    "name": "Кофейня (дефолт)",
    "phone": "+7 900 000-00-00",
    "admin_chat_id": 0,
    "work_start": 9,
    "work_end": 21,
    "menu": {
        "Капучино": 250,
        "Латте": 270,
    },
}

normalized: list[Dict[str, Any]] = []
for cafe in CAFES:
    if not isinstance(cafe, dict):
        continue
    c = dict(DEFAULT_CAFE)
    c.update(cafe)
    c["id"] = str(c.get("id", DEFAULT_CAFE["id"])).strip()
    c["name"] = str(c.get("name", DEFAULT_CAFE["name"]))
    c["phone"] = str(c.get("phone", DEFAULT_CAFE["phone"]))
    c["admin_chat_id"] = int(c.get("admin_chat_id", 0))
    c["work_start"] = int(c.get("work_start", DEFAULT_CAFE["work_start"]))
    c["work_end"] = int(c.get("work_end", DEFAULT_CAFE["work_end"]))
    c["menu"] = dict(c.get("menu", DEFAULT_CAFE["menu"]))
    if c["id"]:
        normalized.append(c)

if not normalized:
    normalized = [DEFAULT_CAFE]

CAFES = normalized
CAFES_BY_ID = {c["id"]: c for c in CAFES}
DEFAULT_CAFE_ID = CAFES[0]["id"]

SUPERADMIN_ID = int(CONFIG.get("superadmin_id") or 0)


# -------------------------
# ENV / WEBHOOK
# -------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "cafebot123")
HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
PORT = int(os.getenv("PORT", 10000))

WEBHOOK_PATH = f"/{WEBHOOK_SECRET}/webhook"
WEBHOOK_URL = f"https://{HOSTNAME}{WEBHOOK_PATH}" if HOSTNAME else None
=======
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
MENU = dict(cafe_config["menu"])

WORK_START = int(cafe_config["work_start"])
WORK_END = int(cafe_config["work_end"])

BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "cafebot123")
HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME", "chatbotify-2tjd.onrender.com")
PORT = int(os.getenv("PORT", 10000))

WEBHOOK_PATH = f"/{WEBHOOK_SECRET}/webhook"
WEBHOOK_URL = f"https://{HOSTNAME}{WEBHOOK_PATH}"
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851

router = Router()


<<<<<<< HEAD
# -------------------------
# Global error handler
# -------------------------

@router.error()
async def on_error(event: ErrorEvent):
    logger.critical("UNHANDLED ERROR in handler: %r", event.exception, exc_info=True)


# -------------------------
# FSM
# -------------------------

=======
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
class OrderStates(StatesGroup):
    waiting_for_quantity = State()
    waiting_for_confirmation = State()


<<<<<<< HEAD
# -------------------------
# Redis helpers
# -------------------------

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


def _user_cafe_key(user_id: int) -> str:
    return f"user_cafe:{user_id}"


def _group_cafe_key(chat_id: int) -> str:
    return f"group_cafe:{chat_id}"


async def get_user_cafe_id(user_id: int) -> Optional[str]:
    r = await get_redis_client()
    try:
        v = await r.get(_user_cafe_key(user_id))
    finally:
        await r.aclose()
    if not v:
        return None
    return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)


async def set_user_cafe_id(user_id: int, cafe_id: str) -> None:
    r = await get_redis_client()
    try:
        await r.set(_user_cafe_key(user_id), cafe_id)
    finally:
        await r.aclose()


async def set_group_cafe_id(chat_id: int, cafe_id: str) -> None:
    r = await get_redis_client()
    try:
        await r.set(_group_cafe_key(chat_id), cafe_id)
    finally:
        await r.aclose()


async def get_group_cafe_id(chat_id: int) -> Optional[str]:
    r = await get_redis_client()
    try:
        v = await r.get(_group_cafe_key(chat_id))
    finally:
        await r.aclose()
    if not v:
        return None
    return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)


def get_cafe_or_default(cafe_id: Optional[str]) -> Dict[str, Any]:
    if cafe_id and cafe_id in CAFES_BY_ID:
        return CAFES_BY_ID[cafe_id]
    return CAFES_BY_ID[DEFAULT_CAFE_ID]


async def get_cafe_for_user(user_id: int) -> Dict[str, Any]:
    cafe_id = await get_user_cafe_id(user_id)
    return get_cafe_or_default(cafe_id)


def is_cafe_open(cafe: Dict[str, Any]) -> bool:
    ws = int(cafe["work_start"])
    we = int(cafe["work_end"])
    return ws <= get_moscow_time().hour < we


def get_work_status(cafe: Dict[str, Any]) -> str:
    ws = int(cafe["work_start"])
    we = int(cafe["work_end"])
    h = get_moscow_time().hour
    if ws <= h < we:
        remaining = max(0, we - h)
        return f"Открыто (ещё {remaining} ч.)"
    return f"Закрыто\nОткрываемся: {ws}:00 (МСК)"


# -------------------------
# Keyboards
# -------------------------

def create_menu_keyboard(cafe: Dict[str, Any]) -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text=drink)] for drink in cafe["menu"].keys()]
    keyboard.append([KeyboardButton(text="Позвонить"), KeyboardButton(text="Часы работы")])
=======
def get_moscow_time() -> datetime:
    return datetime.now(MSK_TZ)


def is_cafe_open() -> bool:
    return WORK_START <= get_moscow_time().hour < WORK_END


def get_work_status() -> str:
    msk_hour = get_moscow_time().hour
    if is_cafe_open():
        remaining = max(0, WORK_END - msk_hour)
        return f"🟢 <b>Открыто</b> (ещё {remaining} ч.)"
    return f"🔴 <b>Закрыто</b>\n🕐 Открываемся: {WORK_START}:00 (МСК)"


# ---------- клавиатуры ----------

def create_menu_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text=drink)] for drink in MENU.keys()]
    keyboard.append([KeyboardButton(text="📞 Позвонить"), KeyboardButton(text="⏰ Часы работы")])
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def create_info_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
<<<<<<< HEAD
        keyboard=[
            [KeyboardButton(text="Позвонить"), KeyboardButton(text="Часы работы")],
        ],
=======
        keyboard=[[KeyboardButton(text="📞 Позвонить"), KeyboardButton(text="⏰ Часы работы")]],
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
        resize_keyboard=True,
    )


<<<<<<< HEAD
def create_admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Мои ссылки")],
            [KeyboardButton(text="Подключить группу")],
            [KeyboardButton(text="Статистика")],
            [KeyboardButton(text="Открыть меню")],
        ],
        resize_keyboard=True,
    )


def create_quantity_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1"), KeyboardButton(text="2"), KeyboardButton(text="3")],
            [KeyboardButton(text="4"), KeyboardButton(text="5"), KeyboardButton(text="Отмена")],
=======
def create_quantity_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1️⃣"), KeyboardButton(text="2️⃣"), KeyboardButton(text="3️⃣")],
            [KeyboardButton(text="4️⃣"), KeyboardButton(text="5️⃣"), KeyboardButton(text="🔙 Отмена")],
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def create_confirm_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Подтвердить"), KeyboardButton(text="Меню")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


<<<<<<< HEAD
# -------------------------
# Warm texts
# -------------------------

WELCOME_VARIANTS = [
    "Рад тебя видеть, {name}! Сегодня что-то классическое или попробуем новинку?",
    "{name}, добро пожаловать! Я уже грею молоко - выбирай, что приготовить.",
=======
# ---------- тёплые тексты ----------

WELCOME_VARIANTS = [
    "Рад тебя видеть, {name}! Сегодня что-то классическое или попробуем новинку?",
    "{name}, добро пожаловать! Я уже грею молоко — выбирай, что приготовить.",
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
    "Заходи, {name}! Сейчас самое время для вкусного перерыва.",
    "{name}, привет! Устроим небольшой кофейный ритуал?",
    "Отлично, что заглянул, {name}! Давай подберём идеальный напиток под настроение.",
]

CHOICE_VARIANTS = [
    "Отличный выбор! Такое сейчас особенно популярно.",
    "Классика, которая никогда не подводит.",
<<<<<<< HEAD
    "Мне тоже нравится этот вариант - не прогадаешь.",
    "Прекрасный вкус, {name}! Это один из хитов нашего меню.",
    "Вот это да, {name}! Любители хорошего кофе тебя поймут.",
    "Смело! Такой выбор обычно делают настоящие ценители.",
    "{name}, ты знаешь толк в напитках.",
    "Звучит вкусно - уже представляю аромат.",
]

FINISH_VARIANTS = [
    "Спасибо за заказ! Буду рад увидеть тебя снова.",
    "Рад был помочь с выбором. Заглядывай ещё - всегда ждём.",
    "Отличный заказ! Надеюсь, это сделает день чуточку лучше.",
    "Спасибо, что выбрал именно нас. До следующей кофейной паузы!",
=======
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
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
    "Заказ готовим с заботой. Возвращайся, когда захочется повторить.",
]


<<<<<<< HEAD
=======
def get_closed_message() -> str:
    menu_text = " • ".join([f"<b>{drink}</b> {price}₽" for drink, price in MENU.items()])
    return (
        f"🔒 <b>{CAFE_NAME} сейчас закрыто!</b>\n\n"
        f"⏰ {get_work_status()}\n\n"
        f"☕ <b>Наше меню:</b>\n{menu_text}\n\n"
        f"📞 <b>Связаться:</b>\n<code>{CAFE_PHONE}</code>\n\n"
        f"✨ <i>До скорой встречи!</i>"
    )


>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
def get_user_name(message: Message) -> str:
    if message.from_user is None:
        return "друг"
    return message.from_user.first_name or "друг"


<<<<<<< HEAD
def get_closed_message(cafe: Dict[str, Any]) -> str:
    menu_text = " • ".join([f"<b>{drink}</b> {price}р" for drink, price in cafe["menu"].items()])
    return (
        f"<b>{cafe['name']} сейчас закрыто!</b>\n\n"
        f"{get_work_status(cafe)}\n\n"
        f"<b>Наше меню:</b>\n{menu_text}\n\n"
        f"<b>Связаться:</b>\n<code>{cafe['phone']}</code>\n\n"
        f"<i>До скорой встречи!</i>"
    )


def is_admin_of_cafe(user_id: int, cafe: Dict[str, Any]) -> bool:
    return user_id == int(cafe["admin_chat_id"]) or (SUPERADMIN_ID and user_id == SUPERADMIN_ID)


# -------------------------
# Debug command
# -------------------------

@router.message(Command("ping"))
async def ping(message: Message):
    await message.answer("pong")


# -------------------------
# Group events + /bind
# -------------------------

@router.my_chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def bot_added_to_group(event: ChatMemberUpdated, bot: Bot):
    if event.chat.type not in ("group", "supergroup"):
        return
    await bot.send_message(
        event.chat.id,
        "Я добавлен в группу.\n\n"
        "Чтобы привязать группу к кафе, напишите:\n"
        "<code>/bind cafe_roma</code>\n\n"
        "Эту команду должен выполнить администратор кафе.",
    )


@router.message(Command("bind"))
async def bind_group(message: Message, command: CommandObject):
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Команда /bind работает только в группе персонала.")
        return

    cafe_id = (command.args or "").strip()
    if not cafe_id:
        await message.answer("Формат: /bind cafe_roma")
        return

    if cafe_id not in CAFES_BY_ID:
        await message.answer("Неизвестный cafe_id. Пример: /bind cafe_roma")
        return

    cafe = CAFES_BY_ID[cafe_id]
    if message.from_user.id != int(cafe["admin_chat_id"]) and (not SUPERADMIN_ID or message.from_user.id != SUPERADMIN_ID):
        await message.answer("Только администратор этого кафе может привязать группу.")
        return

    await set_group_cafe_id(message.chat.id, cafe_id)
    await message.answer(f"Группа привязана к кафе: <b>{cafe['name']}</b>")


# -------------------------
# Admin screens
# -------------------------

async def send_admin_start_screen(message: Message, cafe: Dict[str, Any]):
    guest_link = await create_start_link(message.bot, payload=cafe["id"], encode=False)
    staff_link = await create_startgroup_link(message.bot, payload=cafe["id"], encode=False)

    text = (
        f"<b>Режим администратора</b>\n"
        f"Кафе: <b>{cafe['name']}</b> (id=<code>{cafe['id']}</code>)\n\n"
        f"1) Гостевая ссылка (QR на столы):\n{guest_link}\n\n"
        f"2) Ссылка для группы персонала:\n{staff_link}\n\n"
        f"3) После добавления бота в группу напишите там:\n"
        f"<code>/bind {cafe['id']}</code>\n\n"
        f"Нажмите «Открыть меню», чтобы посмотреть сценарий гостя."
    )
    await message.answer(text, reply_markup=create_admin_keyboard(), disable_web_page_preview=True)


# -------------------------
# START handlers
# -------------------------

async def _start_common(message: Message, state: FSMContext, incoming_cafe_id: Optional[str]):
    await state.clear()
    user_id = message.from_user.id

    if incoming_cafe_id:
        if incoming_cafe_id not in CAFES_BY_ID:
            await message.answer("Ссылка устарела или кафе не найдено. Попросите актуальную ссылку у заведения.")
            return
        await set_user_cafe_id(user_id, incoming_cafe_id)
        cafe = CAFES_BY_ID[incoming_cafe_id]
    else:
        cafe = await get_cafe_for_user(user_id)
        if not await get_user_cafe_id(user_id):
            await set_user_cafe_id(user_id, cafe["id"])

    logger.info(f"/start user={user_id} cafe={cafe['id']} incoming={incoming_cafe_id}")

    if is_admin_of_cafe(user_id, cafe):
        await send_admin_start_screen(message, cafe)
        return

    name = get_user_name(message)
    msk_time = get_moscow_time().strftime("%H:%M")
    welcome = random.choice(WELCOME_VARIANTS).format(name=name)

    if is_cafe_open(cafe):
        await message.answer(
            f"{welcome}\n\n"
            f"<b>{cafe['name']}</b>\n"
            f"<i>Московское время: {msk_time}</i>\n"
            f"{get_work_status(cafe)}\n\n"
            f"<b>Выберите напиток:</b>",
            reply_markup=create_menu_keyboard(cafe),
        )
    else:
        await message.answer(get_closed_message(cafe), reply_markup=create_info_keyboard())


@router.message(CommandStart(deep_link=True))
async def start_with_payload(message: Message, command: CommandObject, state: FSMContext):
    incoming = (command.args or "").strip() or None
    await _start_common(message, state, incoming)


@router.message(CommandStart())
async def start_plain(message: Message, state: FSMContext):
    await _start_common(message, state, None)


# -------------------------
# Admin buttons
# -------------------------

@router.message(F.text == "Открыть меню")
async def open_menu_as_guest(message: Message, state: FSMContext):
    await state.clear()
    cafe = await get_cafe_for_user(message.from_user.id)

    name = get_user_name(message)
    msk_time = get_moscow_time().strftime("%H:%M")
    welcome = random.choice(WELCOME_VARIANTS).format(name=name)

    if is_cafe_open(cafe):
        await message.answer(
            f"{welcome}\n\n"
            f"<b>{cafe['name']}</b>\n"
            f"<i>Московское время: {msk_time}</i>\n"
            f"{get_work_status(cafe)}\n\n"
            f"<b>Выберите напиток:</b>",
            reply_markup=create_menu_keyboard(cafe),
        )
    else:
        await message.answer(get_closed_message(cafe), reply_markup=create_info_keyboard())


@router.message(F.text == "Мои ссылки")
async def my_links_button(message: Message):
    cafe = await get_cafe_for_user(message.from_user.id)
    if not is_admin_of_cafe(message.from_user.id, cafe):
        await message.answer("Доступно только администратору кафе.")
        return
    await send_admin_start_screen(message, cafe)


@router.message(F.text == "Подключить группу")
async def group_help_button(message: Message):
    cafe = await get_cafe_for_user(message.from_user.id)
    if not is_admin_of_cafe(message.from_user.id, cafe):
        await message.answer("Доступно только администратору кафе.")
        return

    staff_link = await create_startgroup_link(message.bot, payload=cafe["id"], encode=False)
    text = (
        "<b>Подключение группы персонала</b>\n\n"
        "1) Создайте группу (например Кафе персонал).\n"
        "2) Добавьте туда бота по ссылке:\n"
        f"{staff_link}\n\n"
        f"3) В группе напишите:\n<code>/bind {cafe['id']}</code>\n"
    )
    await message.answer(text, disable_web_page_preview=True)


@router.message(F.text == "Статистика")
async def stats_button(message: Message):
    await stats_command(message)


# -------------------------
# Ordering
# -------------------------

QUANTITY_MAP = {
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
}


@router.message(F.text)
async def drink_selected(message: Message, state: FSMContext):
    if not message.text:
        return

    cafe = await get_cafe_for_user(message.from_user.id)
    menu = cafe["menu"]

    if message.text not in menu:
        return

    if not is_cafe_open(cafe):
        await message.answer(get_closed_message(cafe), reply_markup=create_info_keyboard())
        return

    drink = message.text
    price = int(menu[drink])

    await state.set_state(OrderStates.waiting_for_quantity)
    await state.set_data({"drink": drink, "price": price, "cafe_id": cafe["id"]})

    choice_text = random.choice(CHOICE_VARIANTS).format(name=get_user_name(message))
    await message.answer(
        f"{choice_text}\n\n"
        f"<b>{drink}</b>\n<b>{price} р</b>\n\n<b>Сколько порций?</b>",
=======
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


# -------------------------
# Пользовательский флоу
# -------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
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


@router.message(F.text.in_(set(MENU.keys())))
async def drink_selected(message: Message, state: FSMContext):
    user_id = message.from_user.id
    name = get_user_name(message)
    logger.info(f"🥤 {message.text} от {user_id}")

    if not is_cafe_open():
        await message.answer(get_closed_message(), reply_markup=create_info_keyboard())
        return

    drink = message.text
    price = MENU[drink]

    await state.set_state(OrderStates.waiting_for_quantity)
    await state.set_data({"drink": drink, "price": price})

    choice_text = random.choice(CHOICE_VARIANTS).format(name=name)

    await message.answer(
        f"{choice_text}\n\n"
        f"🥤 <b>{drink}</b>\n💰 <b>{price} ₽</b>\n\n📝 <b>Сколько порций?</b>",
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
        reply_markup=create_quantity_keyboard(),
    )


@router.message(StateFilter(OrderStates.waiting_for_quantity))
async def process_quantity(message: Message, state: FSMContext):
<<<<<<< HEAD
    cafe = await get_cafe_for_user(message.from_user.id)

    if message.text == "Отмена":
        await state.clear()
        await message.answer(
            "Заказ отменён",
            reply_markup=create_menu_keyboard(cafe) if is_cafe_open(cafe) else create_info_keyboard(),
        )
        return

    quantity = QUANTITY_MAP.get(message.text)
    if quantity:
        data = await state.get_data()
        drink, price = data["drink"], int(data["price"])
        total = price * quantity

        await state.set_state(OrderStates.waiting_for_confirmation)
        await state.update_data(quantity=quantity, total=total)

        await message.answer(
            f"<b>{drink}</b> × {quantity}\nИтого: <b>{total} р</b>\n\nПравильно?",
            reply_markup=create_confirm_keyboard(),
        )
    else:
        await message.answer("Нажмите на кнопку", reply_markup=create_quantity_keyboard())
=======
    if message.text == "🔙 Отмена":
        await state.clear()
        await message.answer(
            "❌ Заказ отменён",
            reply_markup=create_menu_keyboard() if is_cafe_open() else create_info_keyboard(),
        )
        return

    try:
        quantity = int(message.text[0])
        if 1 <= quantity <= 5:
            data = await state.get_data()
            drink, price = data["drink"], data["price"]
            total = price * quantity

            await state.set_state(OrderStates.waiting_for_confirmation)
            await state.update_data(quantity=quantity, total=total)

            await message.answer(
                f"🥤 <b>{drink}</b> × {quantity}\n💰 Итого: <b>{total} ₽</b>\n\n✅ Правильно?",
                reply_markup=create_confirm_keyboard(),
            )
        else:
            await message.answer("❌ Выберите от 1 до 5", reply_markup=create_quantity_keyboard())
    except ValueError:
        await message.answer("❌ Нажмите на кнопку", reply_markup=create_quantity_keyboard())
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851


@router.message(StateFilter(OrderStates.waiting_for_confirmation))
async def process_confirmation(message: Message, state: FSMContext):
<<<<<<< HEAD
    cafe = await get_cafe_for_user(message.from_user.id)
    user_id = message.from_user.id

    if message.text == "Подтвердить":
        try:
            r_client = await get_redis_client()
            last_order = await r_client.get(_rate_limit_key(user_id))
            if last_order and time.time() - float(last_order) < RATE_LIMIT_SECONDS:
                await message.answer(
                    f"Дай мне минутку: новый заказ можно оформить через {RATE_LIMIT_SECONDS} секунд после предыдущего.",
                    reply_markup=create_menu_keyboard(cafe),
=======
    if message.text == "Подтвердить":
        # Rate-limit только после подтверждённого заказа
        try:
            r_client = await get_redis_client()
            user_id = message.from_user.id
            last_order = await r_client.get(_rate_limit_key(user_id))
            if last_order and time.time() - float(last_order) < RATE_LIMIT_SECONDS:
                await message.answer(
                    f"⏳ Дай мне минутку: новый заказ можно оформить через {RATE_LIMIT_SECONDS} секунд после предыдущего.",
                    reply_markup=create_menu_keyboard(),
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
                )
                await r_client.aclose()
                return

            await r_client.setex(_rate_limit_key(user_id), RATE_LIMIT_SECONDS, time.time())
            await r_client.aclose()
        except Exception:
            pass

        data = await state.get_data()
<<<<<<< HEAD
        drink = data["drink"]
        quantity = int(data["quantity"])
        total = int(data["total"])

        order_id = f"order:{int(time.time())}:{user_id}"
        order_num = order_id.split(":")[-1]
        user_name = message.from_user.username or message.from_user.first_name or "Клиент"

=======
        drink, quantity, total = data["drink"], data["quantity"], data["total"]
        order_id = f"order:{int(time.time())}:{message.from_user.id}"
        order_num = order_id.split(":")[-1]

        user_name = message.from_user.username or message.from_user.first_name or "Клиент"
        user_id = message.from_user.id

        # Сохраняем заказ в Redis (stats + детали)
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
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
                    "timestamp": datetime.now().isoformat(),
<<<<<<< HEAD
                    "cafe_id": cafe["id"],
                },
            )
            await r_client.expire(order_id, 86400)
            await r_client.incr(f"stats:{cafe['id']}:total_orders")
            await r_client.incr(f"stats:{cafe['id']}:drink:{drink}")
=======
                },
            )
            await r_client.expire(order_id, 86400)
            await r_client.incr("stats:total_orders")
            await r_client.incr(f"stats:drink:{drink}")
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
            await r_client.aclose()
        except Exception:
            pass

<<<<<<< HEAD
        user_link = f'<a href="tg://user?id={user_id}">{user_name}</a>'
        admin_message = (
            f"<b>НОВЫЙ ЗАКАЗ #{order_num}</b> | {cafe['name']}\n\n"
=======
        # Ссылка, открывающая личный чат с клиентом
        user_link = f'<a href="tg://user?id={user_id}">{user_name}</a>'

        admin_message = (
            f"🔔 <b>НОВЫЙ ЗАКАЗ #{order_num}</b> | {CAFE_NAME}\n\n"
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
            f"{user_link}\n"
            f"<code>{user_id}</code>\n\n"
            f"{drink}\n"
            f"{quantity} порций\n"
<<<<<<< HEAD
            f"<b>{total} р</b>\n\n"
            f"Нажми на имя, чтобы открыть чат и ответить клиенту."
        )

        await message.bot.send_message(int(cafe["admin_chat_id"]), admin_message, disable_web_page_preview=True)

        finish_text = random.choice(FINISH_VARIANTS)
        await message.answer(
            f"<b>Заказ #{order_num} принят!</b>\n\n"
            f"{drink} × {quantity}\n"
            f"{total}р\n\n"
            f"{finish_text}",
            reply_markup=create_menu_keyboard(cafe),
=======
            f"<b>{total} ₽</b>\n\n"
            f"Нажми на имя, чтобы открыть чат и ответить клиенту."
        )

        await message.bot.send_message(
            ADMIN_ID,
            admin_message,
            disable_web_page_preview=True,
        )

        finish_text = random.choice(FINISH_VARIANTS)

        await message.answer(
            f"🎉 <b>Заказ #{order_num} принят!</b>\n\n"
            f"🥤 {drink} × {quantity}\n"
            f"💰 {total}₽\n\n"
            f"{finish_text}",
            reply_markup=create_menu_keyboard(),
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
        )
        await state.clear()
        return

    if message.text == "Меню":
        await state.clear()
<<<<<<< HEAD
        await message.answer("Меню:", reply_markup=create_menu_keyboard(cafe))
        return

    await message.answer("Нажмите кнопку", reply_markup=create_confirm_keyboard())


# -------------------------
# Info buttons
# -------------------------

@router.message(F.text == "Позвонить")
async def call_phone(message: Message):
    cafe = await get_cafe_for_user(message.from_user.id)
    name = get_user_name(message)

    if is_cafe_open(cafe):
        await message.answer(
            f"{name}, буду рад помочь!\n\n"
            f"<b>Телефон {cafe['name']}:</b>\n<code>{cafe['phone']}</code>\n",
            reply_markup=create_menu_keyboard(cafe),
        )
    else:
        await message.answer(
            f"{name}, сейчас мы закрыты.\n\n"
            f"<b>Телефон {cafe['name']}:</b>\n<code>{cafe['phone']}</code>\n\n"
            f"{get_work_status(cafe)}\n",
            reply_markup=create_info_keyboard(),
        )


@router.message(F.text == "Часы работы")
async def show_hours(message: Message):
    cafe = await get_cafe_for_user(message.from_user.id)
    name = get_user_name(message)
    msk_time = get_moscow_time().strftime("%H:%M")

    await message.answer(
        f"{name}, вот режим работы:\n\n"
        f"<b>Сейчас:</b> {msk_time} (МСК)\n"
        f"{get_work_status(cafe)}\n\n"
        f"Телефон: <code>{cafe['phone']}</code>\n",
        reply_markup=create_menu_keyboard(cafe) if is_cafe_open(cafe) else create_info_keyboard(),
    )


# -------------------------
# Commands
# -------------------------

@router.message(Command("stats"))
async def stats_command(message: Message):
    cafe = await get_cafe_for_user(message.from_user.id)
    if not is_admin_of_cafe(message.from_user.id, cafe):
        return

    try:
        r_client = await get_redis_client()
        total_orders = int(await r_client.get(f"stats:{cafe['id']}:total_orders") or 0)
        stats_text = (
            f"<b>Статистика заказов</b>\n"
            f"Кафе: <b>{cafe['name']}</b> (id={cafe['id']})\n\n"
            f"Всего заказов: <b>{total_orders}</b>\n\n"
        )
        for drink in cafe["menu"].keys():
            count = int(await r_client.get(f"stats:{cafe['id']}:drink:{drink}") or 0)
=======
        await message.answer("☕ Меню:", reply_markup=create_menu_keyboard())
        return

    await message.answer("❌ Нажмите кнопку", reply_markup=create_confirm_keyboard())


@router.message(F.text == "📞 Позвонить")
async def call_phone(message: Message):
    name = get_user_name(message)
    if is_cafe_open():
        text = (
            f"{name}, буду рад помочь!\n\n"
            f"📞 <b>Телефон {CAFE_NAME}:</b>\n<code>{CAFE_PHONE}</code>\n\n"
            f"Если удобнее — можешь просто выбрать напиток в меню, я всё оформлю здесь."
        )
        await message.answer(text, reply_markup=create_menu_keyboard())
    else:
        text = (
            f"{name}, сейчас мы закрыты, но я всё равно подскажу.\n\n"
            f"📞 <b>Телефон {CAFE_NAME}:</b>\n<code>{CAFE_PHONE}</code>\n\n"
            f"⏰ {get_work_status()}\n\n"
            f"Хочешь — посмотри меню, а заказ оформим, как только откроемся."
        )
        await message.answer(text, reply_markup=create_info_keyboard())


@router.message(F.text == "⏰ Часы работы")
async def show_hours(message: Message):
    name = get_user_name(message)
    msk_time = get_moscow_time().strftime("%H:%M")
    if is_cafe_open():
        text = (
            f"{name}, мы сейчас на месте и готовим вкусное.\n\n"
            f"🕐 <b>Сейчас:</b> {msk_time} (МСК)\n"
            f"🏪 {get_work_status()}\n\n"
            f"📞 Если нужно уточнить детали: <code>{CAFE_PHONE}</code>\n"
            f"Выбирай напиток в меню — оформлю заказ за минуту."
        )
        await message.answer(text, reply_markup=create_menu_keyboard())
    else:
        text = (
            f"{name}, спасибо что заглянул!\n\n"
            f"🕐 <b>Сейчас:</b> {msk_time} (МСК)\n"
            f"🏪 {get_work_status()}\n\n"
            f"📞 Телефон: <code>{CAFE_PHONE}</code>\n"
            f"Пока можем показать меню — напиши /start."
        )
        await message.answer(text, reply_markup=create_info_keyboard())


@router.message(Command("stats"))
async def stats_command(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        r_client = await get_redis_client()
        total_orders = int(await r_client.get("stats:total_orders") or 0)
        stats_text = f"📊 <b>Статистика заказов</b>\n\nВсего заказов: <b>{total_orders}</b>\n\n"
        for drink in MENU.keys():
            count = int(await r_client.get(f"stats:drink:{drink}") or 0)
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
            if count > 0:
                stats_text += f"{drink}: {count}\n"
        await r_client.aclose()
        await message.answer(stats_text)
    except Exception:
<<<<<<< HEAD
        await message.answer("Ошибка статистики")


@router.message(Command("links"))
async def links_command(message: Message):
    if not SUPERADMIN_ID or message.from_user.id != SUPERADMIN_ID:
        return

    parts = ["<b>Ссылки всех кафе</b>\n"]
    for cafe in CAFES:
        guest_link = await create_start_link(message.bot, payload=cafe["id"], encode=False)
        staff_link = await create_startgroup_link(message.bot, payload=cafe["id"], encode=False)
        parts.append(
            f"<b>{cafe['name']}</b> (id={cafe['id']}):\n"
            f"Гости: {guest_link}\n"
            f"Персонал: {staff_link}\n"
        )
    await message.answer("\n".join(parts), disable_web_page_preview=True)


@router.message(Command("myid"))
async def myid(message: Message):
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")
=======
        await message.answer("❌ Ошибка статистики")


# -------------------------
# /help c фишкой “тёплые ответы”
# -------------------------

@router.message(Command("help"))
async def help_command(message: Message):
    text = (
        "Этот бот — демо-ассистент для кофейни.\n\n"
        "Что он умеет:\n"
        "• Показывать меню и часы работы\n"
        "• Принимать быстрые заказы прямо в чате\n"
        "• Отправлять уведомления администратору с данными клиента\n"
        "• Позволять администратору сразу открыть чат с гостем\n\n"
        "Наша фишка — тёплые ответы:\n"
        "• Разные варианты приветствия в начале диалога\n"
        "• Живые комментарии к выбору напитка\n"
        "• Дружелюбные завершения заказа с приглашением вернуться\n"
        "Бот говорит с гостем так, будто это внимательный бариста.\n\n"
        "Для владельцев кафе:\n"
        "• Это демо-версия сервиса CafeBotify — «бот вместо администратора»\n"
        "• Подключение тарифа START - 2 990 ₽ в месяц за одну точку (такой же набор функций)\n\n"
        "Хотите такой бот для своей кофейни?\n"
        "Связаться в Telegram: @denvyd"
    )
    await message.answer(text)
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851


# -------------------------
# Startup / Webhook
# -------------------------

<<<<<<< HEAD
async def set_bot_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="Запуск бота"),
        BotCommand(command="ping", description="Проверка (pong)"),
        BotCommand(command="myid", description="Показать мой Telegram ID"),
        BotCommand(command="stats", description="Статистика (админ)"),
        BotCommand(command="bind", description="Привязать группу к кафе (в группе)"),
        BotCommand(command="links", description="Ссылки всех кафе (суперадмин)"),
    ]
    await bot.set_my_commands(commands)


async def on_startup(bot: Bot) -> None:
    logger.info("=== BUILD MARK: MULTI-CAFE MAIN v4 (no em-dash) ===")
    logger.info(f"Cafes loaded: {len(CAFES)}")
    for c in CAFES:
        logger.info(f"CFG cafe={c['id']} admin={c['admin_chat_id']}")

    if WEBHOOK_URL:
        logger.info(f"Webhook target: {WEBHOOK_URL}")
=======
async def on_startup(bot: Bot) -> None:
    logger.info("🚀 Запуск бота (START v1.0 DEMO)...")
    logger.info(f"☕ Кафе: {CAFE_NAME}")
    logger.info(f"⏰ Часы работы: {WORK_START}:00–{WORK_END}:00 (МСК)")
    logger.info(f"⏳ Rate-limit: {RATE_LIMIT_SECONDS} сек. (только после подтверждения)")
    logger.info(f"🔗 Webhook (target): {WEBHOOK_URL}")
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851

    try:
        r_test = redis.from_url(REDIS_URL)
        await r_test.ping()
        await r_test.aclose()
<<<<<<< HEAD
        logger.info("Redis connected")
    except Exception as e:
        logger.error(f"Redis error: {e}")

    try:
        await set_bot_commands(bot)
        logger.info("Commands set")
    except Exception as e:
        logger.error(f"set_my_commands error: {e}")

    if WEBHOOK_URL:
        try:
            await bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET)
            logger.info("Webhook set")
        except Exception as e:
            logger.error(f"Webhook error: {e}")
    else:
        logger.warning("WEBHOOK_URL is None (no RENDER_EXTERNAL_HOSTNAME). Webhook not set.")

    try:
        for cafe in CAFES:
            guest = await create_start_link(bot, payload=cafe["id"], encode=False)
            staff = await create_startgroup_link(bot, payload=cafe["id"], encode=False)
            logger.info(f"LINK guest [{cafe['id']}]: {guest}")
            logger.info(f"LINK staff  [{cafe['id']}]: {staff}")
    except Exception as e:
        logger.error(f"Link generation error: {e}")
=======
        logger.info("✅ Redis подключён")
    except Exception as e:
        logger.error(f"❌ Redis: {e}")

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
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851


async def main():
    if not BOT_TOKEN:
<<<<<<< HEAD
        logger.error("BOT_TOKEN not found")
        return
    if not REDIS_URL:
        logger.error("REDIS_URL not found")
=======
        logger.error("❌ BOT_TOKEN не найден!")
        return
    if not REDIS_URL:
        logger.error("❌ REDIS_URL не найден!")
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
        return

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    storage = RedisStorage.from_url(REDIS_URL)
    dp = Dispatcher(storage=storage)
    dp.include_router(router)
<<<<<<< HEAD
=======

>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851
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
<<<<<<< HEAD
        logger.info("Shutdown complete")
=======
        logger.info("🛑 Бот остановлен")
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851

    app.on_shutdown.append(_on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
<<<<<<< HEAD
    await site.start()

    logger.info(f"Server running on 0.0.0.0:{PORT}")
    await asyncio.Event().wait()
=======

    logger.info(f"🌐 Сервер запущен на 0.0.0.0:{PORT}")
    await site.start()

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
>>>>>>> 6a5d87f32643b0642d29376cb373f25ab5ae8851


if __name__ == "__main__":
    asyncio.run(main())
