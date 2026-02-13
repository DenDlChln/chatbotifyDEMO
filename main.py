# =========================
# CafeBotify — START v1.0 (DEMO)
# Меню и часы работы из config.json
# Rate-limit: 1 минута, ставится только после подтверждённого заказа
# NEW/DEMO:
# - Кнопка 📊 Статистика видна всем в DEMO
# - После подтверждения заказа пользователь видит "как видит админ" (2 сообщения, как на скрине)
# - Админу также приходит реальное уведомление + пояснение
# =========================

import os
import json
import logging
import asyncio
import time
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple

import redis.asyncio as redis
from aiohttp import web

from aiogram import Bot, Dispatcher, F, Router
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# Для безопасной HTML-разметки
from aiogram import html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

MSK_TZ = timezone(timedelta(hours=3))
RATE_LIMIT_SECONDS = 60

# В DEMO пользователь после заказа видит "админский" формат у себя в чате
DEMO_MODE = True


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

router = Router()


class OrderStates(StatesGroup):
    waiting_for_quantity = State()
    waiting_for_confirmation = State()


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


# ---------- клавиатуры ----------

def create_menu_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text=drink)] for drink in MENU.keys()]
    keyboard.append(
        [
            KeyboardButton(text="📞 Позвонить"),
            KeyboardButton(text="⏰ Часы работы"),
            KeyboardButton(text="📊 Статистика"),  # DEMO: видна всем
        ]
    )
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def create_info_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(text="📞 Позвонить"),
            KeyboardButton(text="⏰ Часы работы"),
            KeyboardButton(text="📊 Статистика"),  # DEMO: видна всем
        ]],
        resize_keyboard=True,
    )


def create_quantity_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1️⃣"), KeyboardButton(text="2️⃣"), KeyboardButton(text="3️⃣")],
            [KeyboardButton(text="4️⃣"), KeyboardButton(text="5️⃣"), KeyboardButton(text="🔙 Отмена")],
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
    menu_text = " • ".join([f"<b>{drink}</b> {price}₽" for drink, price in MENU.items()])
    return (
        f"🔒 <b>{CAFE_NAME} сейчас закрыто!</b>\n\n"
        f"⏰ {get_work_status()}\n\n"
        f"☕ <b>Наше меню:</b>\n{menu_text}\n\n"
        f"📞 <b>Связаться:</b>\n<code>{CAFE_PHONE}</code>\n\n"
        f"✨ <i>До скорой встречи!</i>"
    )


def get_user_name(message: Message) -> str:
    if message.from_user is None:
        return "друг"
    return message.from_user.first_name or "друг"


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
        f"🥤 <b>{html.quote(drink)}</b>\n💰 <b>{price} ₽</b>\n\n📝 <b>Сколько порций?</b>",
        reply_markup=create_quantity_keyboard(),
    )


@router.message(StateFilter(OrderStates.waiting_for_quantity))
async def process_quantity(message: Message, state: FSMContext):
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
                f"🥤 <b>{html.quote(drink)}</b> × {quantity}\n💰 Итого: <b>{total} ₽</b>\n\n✅ Правильно?",
                reply_markup=create_confirm_keyboard(),
            )
        else:
            await message.answer("❌ Выберите от 1 до 5", reply_markup=create_quantity_keyboard())
    except ValueError:
        await message.answer("❌ Нажмите на кнопку", reply_markup=create_quantity_keyboard())


def _build_admin_order_messages(
    *,
    order_num: str,
    user_id: int,
    user_name: str,
    drink: str,
    quantity: int,
    total: int,
) -> tuple[str, str]:
    safe_user_name = html.quote(user_name)
    safe_drink = html.quote(drink)

    user_link = f'<a href="tg://user?id={user_id}">{safe_user_name}</a>'

    admin_message = (
        f"🔔 <b>НОВЫЙ ЗАКАЗ #{order_num}</b> | {html.quote(CAFE_NAME)}\n\n"
        f"{user_link}\n"
        f"<code>{user_id}</code>\n\n"
        f"{safe_drink}\n"
        f"{quantity} порций\n"
        f"<b>{total} ₽</b>\n\n"
        f"Нажми на имя, чтобы открыть чат и ответить клиенту."
    )

    admin_demo_message = (
        "ℹ️ <b>ПРИМЕР ПОДТВЕРЖДЁННОГО ЗАКАЗА (КАК ВИДИТ АДМИН)</b>\n\n"
        "В рабочем режиме бот будет присылать вам каждое подтверждение заказа в таком виде.\n"
        f"Выше — только что оформленный заказ гостя #{order_num}.\n\n"
        "Нажмите на имя клиента, чтобы открыть чат и уточнить детали."
    )

    return admin_message, admin_demo_message


@router.message(StateFilter(OrderStates.waiting_for_confirmation))
async def process_confirmation(message: Message, state: FSMContext):
    if message.text == "Подтвердить":
        user_id = message.from_user.id

        # rate-limit после подтверждения
        try:
            r_client = await get_redis_client()
            last_order = await r_client.get(_rate_limit_key(user_id))
            if last_order and time.time() - float(last_order) < RATE_LIMIT_SECONDS:
                await message.answer(
                    f"⏳ Дай мне минутку: новый заказ можно оформить через {RATE_LIMIT_SECONDS} секунд после предыдущего.",
                    reply_markup=create_menu_keyboard(),
                )
                await r_client.aclose()
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
                    "timestamp": datetime.now().isoformat(),
                },
            )
            await r_client.expire(order_id, 86400)
            await r_client.incr("stats:total_orders")
            await r_client.incr(f"stats:drink:{drink}")
            await r_client.aclose()
        except Exception:
            pass

        admin_message, admin_demo_message = _build_admin_order_messages(
            order_num=order_num,
            user_id=user_id,
            user_name=user_name,
            drink=drink,
            quantity=quantity,
            total=total,
        )

        # 1) Отправляем админу (как и раньше)
        try:
            await message.bot.send_message(ADMIN_ID, admin_message, disable_web_page_preview=True)
            await message.bot.send_message(ADMIN_ID, admin_demo_message, disable_web_page_preview=True)
        except Exception as e:
            logger.exception(f"ADMIN notify failed (ADMIN_ID={ADMIN_ID}): {e}")

        # 2) DEMO: показываем пользователю тот же "админский" формат у него в чате
        if DEMO_MODE:
            await message.answer(admin_message, reply_markup=create_menu_keyboard())
            await message.answer(admin_demo_message, reply_markup=create_menu_keyboard())

        finish_text = random.choice(FINISH_VARIANTS).format(name=get_user_name(message))

        await message.answer(
            f"🎉 <b>Заказ #{order_num} принят!</b>\n\n"
            f"🥤 {html.quote(drink)} × {quantity}\n"
            f"💰 {total}₽\n\n"
            f"{finish_text}",
            reply_markup=create_menu_keyboard(),
        )
        await state.clear()
        return

    if message.text == "Меню":
        await state.clear()
        await message.answer("☕ Меню:", reply_markup=create_menu_keyboard())
        return

    await message.answer("❌ Нажмите кнопку", reply_markup=create_confirm_keyboard())


@router.message(F.text == "📞 Позвонить")
async def call_phone(message: Message):
    name = get_user_name(message)
    if is_cafe_open():
        text = (
            f"{name}, буду рад помочь!\n\n"
            f"📞 <b>Телефон {html.quote(CAFE_NAME)}:</b>\n<code>{html.quote(CAFE_PHONE)}</code>\n\n"
            f"Если удобнее — можешь просто выбрать напиток в меню, я всё оформлю здесь."
        )
        await message.answer(text, reply_markup=create_menu_keyboard())
    else:
        text = (
            f"{name}, сейчас мы закрыты, но я всё равно подскажу.\n\n"
            f"📞 <b>Телефон {html.quote(CAFE_NAME)}:</b>\n<code>{html.quote(CAFE_PHONE)}</code>\n\n"
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
            f"📞 Если нужно уточнить детали: <code>{html.quote(CAFE_PHONE)}</code>\n"
            f"Выбирай напиток в меню — оформлю заказ за минуту."
        )
        await message.answer(text, reply_markup=create_menu_keyboard())
    else:
        text = (
            f"{name}, спасибо что заглянул!\n\n"
            f"🕐 <b>Сейчас:</b> {msk_time} (МСК)\n"
            f"🏪 {get_work_status()}\n\n"
            f"📞 Телефон: <code>{html.quote(CAFE_PHONE)}</code>\n"
            f"Пока можем показать меню — напиши /start."
        )
        await message.answer(text, reply_markup=create_info_keyboard())


@router.message(Command("stats"))
async def stats_command(message: Message):
    # /stats оставим как "реальная админская команда"
    if message.from_user.id != ADMIN_ID:
        if DEMO_MODE:
            await _send_demo_stats(message)
        return
    await _send_real_stats(message)


@router.message(F.text == "📊 Статистика")
async def stats_button(message: Message):
    # В DEMO кнопку видят все: админу — реальная, пользователю — пример
    if message.from_user.id == ADMIN_ID:
        await _send_real_stats(message)
    else:
        await _send_demo_stats(message)


@router.message(Command("help"))
async def help_command(message: Message):
    text = (
        "Этот бот — демо-ассистент для кофейни.\n\n"
        "Что он умеет:\n"
        "• Показывать меню и часы работы\n"
        "• Принимать быстрые заказы прямо в чате\n"
        "• Отправлять уведомления администратору с данными клиента\n"
        "• Показывать пример того, как админ видит подтверждённый заказ (DEMO)\n"
        "• Показывать статистику (в демо — пример)\n\n"
        "Хотите такой бот для своей кофейни?\n"
        "Связаться в Telegram: @denvyd"
    )
    await message.answer(text, reply_markup=create_menu_keyboard())


# -------------------------
# Startup / Webhook
# -------------------------

async def on_startup(bot: Bot) -> None:
    logger.info("🚀 Запуск бота (START v1.0 DEMO)...")
    logger.info(f"☕ Кафе: {CAFE_NAME}")
    logger.info(f"⏰ Часы работы: {WORK_START}:00–{WORK_END}:00 (МСК)")
    logger.info(f"⏳ Rate-limit: {RATE_LIMIT_SECONDS} сек. (только после подтверждения)")
    logger.info(f"🔗 Webhook (target): {WEBHOOK_URL}")

    try:
        r_test = redis.from_url(REDIS_URL)
        await r_test.ping()
        await r_test.aclose()
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
