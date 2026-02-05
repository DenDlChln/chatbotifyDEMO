import os
import json
import logging
import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

import redis.asyncio as redis
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

MSK_TZ = timezone(timedelta(hours=3))
WORK_START = 9
WORK_END = 21

def load_config() -> Dict[str, Any]:
    default_config = {
        "name": "Кофейня «Уют» ☕",
        "phone": "+7 989 273-67-56", 
        "admin_chat_id": 1471275603,
        "menu": {
            "☕ Капучино": 250,
            "🥛 Латте": 270,
            "🍵 Чай": 180,
            "⚡ Эспрессо": 200
        }
    }
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            config = data.get('cafe', {})
            default_config.update({
                'name': config.get('name', default_config['name']),
                'phone': config.get('phone', default_config['phone']),
                'admin_chat_id': config.get('admin_chat_id', default_config['admin_chat_id']),
                'menu': config.get('menu', default_config['menu'])
            })
    except Exception:
        pass
    return default_config

cafe_config = load_config()
CAFE_NAME = cafe_config["name"]
CAFE_PHONE = cafe_config["phone"]
ADMIN_ID = int(cafe_config["admin_chat_id"])
MENU = dict(cafe_config["menu"])

BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "cafebot123")
HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME', 'chatbotify-2tjd.onrender.com')
PORT = int(os.getenv('PORT', 10000))

WEBHOOK_PATH = f'/{WEBHOOK_SECRET}/webhook'
WEBHOOK_URL = f"https://{HOSTNAME}{WEBHOOK_PATH}"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
storage = RedisStorage.from_url(REDIS_URL)
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

class OrderStates(StatesGroup):
    waiting_for_quantity = State()
    waiting_for_confirmation = State()

def get_moscow_time() -> datetime:
    return datetime.now(MSK_TZ)

def is_cafe_open() -> bool:
    return WORK_START <= get_moscow_time().hour < WORK_END

def get_work_status() -> str:
    msk_hour = get_moscow_time().hour
    if is_cafe_open():
        return f"🟢 <b>Открыто</b> (ещё {WORK_END-msk_hour} ч.)"
    return f"🔴 <b>Закрыто</b>\n🕐 Открываемся: {WORK_START}:00 (МСК)"

def create_menu_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text=drink)] for drink in MENU.keys()]
    keyboard.append([KeyboardButton(text="📞 Позвонить"), KeyboardButton(text="⏰ Часы работы")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def create_info_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📞 Позвонить"), KeyboardButton(text="⏰ Часы работы")]],
        resize_keyboard=True
    )

def create_quantity_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1️⃣"), KeyboardButton(text="2️⃣"), KeyboardButton(text="3️⃣")],
            [KeyboardButton(text="4️⃣"), KeyboardButton(text="5️⃣"), KeyboardButton(text="🔙 Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def create_confirm_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Подтвердить"), KeyboardButton(text="Меню")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_closed_message() -> str:
    menu_text = " • ".join([f"<b>{drink}</b> {price}₽" for drink, price in MENU.items()])
    return f"🔒 <b>{CAFE_NAME} сейчас закрыто!</b>\n\n⏰ {get_work_status()}\n\n☕ <b>Наше меню:</b>\n{menu_text}\n\n📞 <b>Связаться:</b>\n<code>{CAFE_PHONE}</code>\n\n✨ <i>До скорой встречи!</i>"

async def get_redis_client():
    client = redis.from_url(REDIS_URL)
    try:
        await client.ping()
        return client
    except:
        await client.aclose()
        raise

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    msk_time = get_moscow_time().strftime("%H:%M")
    logger.info(f"👤 /start от {user_id} | MSK: {msk_time}")
    
    if is_cafe_open():
        await message.answer(
            f"<b>{CAFE_NAME}</b>\n\n🕐 <i>Московское время: {msk_time}</i>\n🏪 {get_work_status()}\n\n☕ <b>Выберите напиток:</b>",
            reply_markup=create_menu_keyboard()
        )
    else:
        await message.answer(get_closed_message(), reply_markup=create_info_keyboard())

@router.message(F.text.in_(set(MENU.keys())))
async def drink_selected(message: Message, state: FSMContext):
    user_id = message.from_user.id
    logger.info(f"🥤 {message.text} от {user_id}")
    
    if not is_cafe_open():
        await message.answer(get_closed_message(), reply_markup=create_info_keyboard())
        return
    
    try:
        r_client = await get_redis_client()
        last_order = await r_client.get(f"rate_limit:{user_id}")
        if last_order and time.time() - float(last_order) < 300:
            await message.answer("⏳ Подождите 5 минут перед новым заказом", reply_markup=create_menu_keyboard())
            await r_client.aclose()
            return
        await r_client.setex(f"rate_limit:{user_id}", 300, time.time())
        await r_client.aclose()
    except:
        pass
    
    drink = message.text
    price = MENU[drink]
    
    await state.set_state(OrderStates.waiting_for_quantity)
    await state.set_data({"drink": drink, "price": price})
    
    await message.answer(
        f"🥤 <b>{drink}</b>\n💰 <b>{price} ₽</b>\n\n📝 <b>Сколько порций?</b>",
        reply_markup=create_quantity_keyboard()
    )

@router.message(StateFilter(OrderStates.waiting_for_quantity))
async def process_quantity(message: Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.clear()
        await message.answer("❌ Заказ отменён", reply_markup=create_menu_keyboard() if is_cafe_open() else create_info_keyboard())
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
                reply_markup=create_confirm_keyboard()
            )
        else:
            await message.answer("❌ Выберите от 1 до 5", reply_markup=create_quantity_keyboard())
    except ValueError:
        await message.answer("❌ Нажмите на кнопку", reply_markup=create_quantity_keyboard())

@router.message(StateFilter(OrderStates.waiting_for_confirmation))
async def process_confirmation(message: Message, state: FSMContext):
    if message.text == "Подтвердить":
        data = await state.get_data()
        drink, quantity, total = data["drink"], data["quantity"], data["total"]
        order_id = f"order:{int(time.time())}:{message.from_user.id}"
        order_num = order_id.split(':')[-1]
        
        try:
            r_client = await get_redis_client()
            await r_client.hset(order_id, mapping={
                "user_id": message.from_user.id,
                "username": message.from_user.username or "N/A",
                "drink": drink,
                "quantity": quantity,
                "total": total,
                "timestamp": datetime.now().isoformat()
            })
            await r_client.expire(order_id, 86400)
            await r_client.incr("stats:total_orders")
            await r_client.incr(f"stats:drink:{drink}")
            await r_client.aclose()
        except:
            pass
        
        user_name = message.from_user.username or message.from_user.first_name or "Клиент"
        admin_message = (
            f"🔔 <b>НОВЫЙ ЗАКАЗ #{order_num}</b> | {CAFE_NAME}\n\n"
            f"<b>{user_name}</b>\n"
            f"<code>{message.from_user.id}</code>\n\n"
            f"{drink}\n"
            f"{quantity} порций\n"
            f"<b>{total} ₽</b>\n\n"
            f"<code>{CAFE_PHONE}</code>"
        )
        
        await bot.send_message(ADMIN_ID, admin_message, disable_web_page_preview=True)
        
        await message.answer(
            f"🎉 <b>Заказ #{order_num} принят!</b>\n\n"
            f"🥤 {drink} × {quantity}\n"
            f"💰 {total}₽\n\n"
            f"📞 {CAFE_PHONE}\n⏳ Готовим!",
            reply_markup=create_menu_keyboard()
        )
        await state.clear()
    elif message.text == "Меню":
        await state.clear()
        await message.answer("☕ Меню:", reply_markup=create_menu_keyboard())
    else:
        await message.answer("❌ Нажмите кнопку", reply_markup=create_confirm_keyboard())

@router.message(F.text == "📞 Позвонить")
async def call_phone(message: Message):
    await message.answer(f"📞 Звоните: <code>{CAFE_PHONE}</code>")

@router.message(F.text == "⏰ Часы работы")
async def show_hours(message: Message):
    await message.answer(f"🏪 {get_work_status()}\n📞 {CAFE_PHONE}")

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
            if count > 0:
                stats_text += f"{drink}: {count}\n"
        await r_client.aclose()
        await message.answer(stats_text)
    except:
        await message.answer("❌ Ошибка статистики")

async def on_startup(app: web.Application):
    logger.info("🚀 Запуск бота...")
    logger.info(f"☕ Кафе: {CAFE_NAME}")
    logger.info(f"🔗 Webhook: {WEBHOOK_URL}")
    
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
        if current_webhook.url != WEBHOOK_URL:
            await bot.set_webhook(WEBHOOK_URL)
            logger.info(f"✅ Webhook установлен: {WEBHOOK_URL}")
        else:
            logger.info("ℹ️ Webhook уже установлен")
    except Exception as e:
        logger.error(f"❌ Webhook ошибка: {e}")

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await storage.close()
    logger.info("🛑 Бот остановлен")

async def webhook_handler(request: web.Request):
    try:
        update = await request.json()
        await dp.feed_update(bot, update)
        return web.json_response({"status": "ok"}, status=200)
    except Exception as e:
        logger.error(f"Webhook ошибка: {e}")
        return web.json_response({"error": "internal error"}, status=500)

async def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не найден!")
        return
    if not REDIS_URL:
        logger.error("❌ REDIS_URL не найден!")
        return
    
    app = web.Application()
    
    async def healthcheck(request):
        return web.json_response({"status": "healthy", "bot": "ready"})
    
    app.router.add_get('/', healthcheck)
    app.router.add_post(WEBHOOK_PATH, webhook_handler)
    
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    
    logger.info(f"🌐 Сервер запущен на 0.0.0.0:{PORT}")
    await site.start()
    
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
