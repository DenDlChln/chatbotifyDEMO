import os
import json
import logging
import asyncio
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiohttp import web
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import time

# ========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ✅ МОСКОВСКОЕ ВРЕМЯ (UTC+3)
MSK_TZ = timezone(timedelta(hours=3))
WORK_START = 9
WORK_END = 21

# Антиспам: user_id → timestamp последнего заказа
last_orders = defaultdict(float)

# ========================================
def load_config():
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            config = data.get('cafe', {})
            return {
                'name': config.get('name', 'Кофейня «Уют» ☕'),
                'phone': config.get('phone', '+7 989 273-67-56'),
                'admin_chat_id': config.get('admin_chat_id', 1471275603),
                'menu': config.get('menu', {
                    "☕ Капучино": 250,
                    "🥛 Латте": 270,
                    "🍵 Чай": 180,
                    "⚡ Эспрессо": 200
                })
            }
    except:
        return {
            "name": "Кофейня «Уют» ☕",
            "phone": "+7 989 273-67-56",
            "admin_chat_id": 1471275603,
            "menu": {"☕ Капучино": 250, "🥛 Латте": 270, "🍵 Чай": 180, "⚡ Эспрессо": 200}
        }

cafe_config = load_config()
CAFE_NAME = cafe_config["name"]
CAFE_PHONE = cafe_config["phone"]
ADMIN_ID = int(cafe_config["admin_chat_id"])
MENU = dict(cafe_config["menu"])

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBAPP_HOST = os.getenv('WEBAPP_HOST', 'chatbotify-2tjd.onrender.com')
WEBAPP_PORT = int(os.getenv('PORT', 10000))
WEBHOOK_PATH = f'/{BOT_TOKEN}'
WEBHOOK_URL = f'https://{WEBAPP_HOST}/{BOT_TOKEN}'

# ========================================
bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

class OrderStates(StatesGroup):
    waiting_for_quantity = State()
    waiting_for_confirmation = State()

# ========================================
def get_moscow_time():
    return datetime.now(MSK_TZ)

def is_cafe_open():
    msk_hour = get_moscow_time().hour
    return WORK_START <= msk_hour < WORK_END

def get_work_status():
    msk_hour = get_moscow_time().hour
    if is_cafe_open():
        time_left = WORK_END - msk_hour
        return f"🟢 <b>Открыто</b> (ещё {time_left} ч.)"
    else:
        next_open = f"{WORK_START}:00"
        return f"🔴 <b>Закрыто</b>\\n🕐 Открываемся: {next_open} (МСК)"

def get_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    for drink in MENU: 
        kb.add(drink)
    kb.row("📞 Позвонить", "⏰ Часы работы")
    return kb

def get_info_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.row("📞 Позвонить", "⏰ Часы работы")
    return kb

def get_quantity_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=3)
    kb.add("1️⃣", "2️⃣", "3️⃣").add("4️⃣", "5️⃣", "🔙 Отмена")
    return kb

def get_confirm_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    kb.row("✅ Подтвердить", "📝 Меню")  # ✅ ИСПРАВЛЕНО
    return kb

def get_correct_keyboard():
    return get_menu_keyboard() if is_cafe_open() else get_info_keyboard()

def get_closed_message():
    """🔒 Закрытие с МЕНЮ + До скорой встречи!"""
    menu_text = "• " + " | ".join([f"<b>{drink}</b> {MENU[drink]}₽" for drink in MENU])
    
    return (
        f"🔒 <b>{CAFE_NAME} сейчас закрыто!</b>\\n\\n"
        f"⏰ {get_work_status()}\\n\\n"
        f"☕ <b>Наше меню:</b>\\n"
        f"{menu_text}\\n\\n"
        f"📞 <b>Связаться:</b>\\n<code>{CAFE_PHONE}</code>\\n\\n"
        f"✨ <i>До скорой встречи!</i>"
    )

# ========================================
@dp.message_handler(commands=['start', 'help'])
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    msk_time = get_moscow_time().strftime("%H:%M")
    logger.info(f"👤 /start от {message.from_user.id} | MSK: {msk_time}")
    
    if is_cafe_open():
        await message.answer(
            f"<b>{CAFE_NAME}</b>\\n\\n"
            f"🕐 <i>Московское время: {msk_time}</i>\\n"
            f"🏪 {get_work_status()}\\n\\n"
            f"☕ <b>Выберите напиток:</b>",
            reply_markup=get_menu_keyboard()
        )
    else:
        await message.answer(get_closed_message(), reply_markup=get_info_keyboard())

@dp.message_handler(lambda m: m.text in MENU)
async def drink_selected(message: types.Message, state: FSMContext):
    logger.info(f"🥤 {message.text} от {message.from_user.id}")
    
    if not is_cafe_open():
        await message.answer(get_closed_message(), reply_markup=get_info_keyboard())
        return
    
    # ✅ АНТИСПАМ
    if time.time() - last_orders[message.from_user.id] < 300:  # 5 мин
        await message.answer("⏳ Подождите 5 минут перед новым заказом", reply_markup=get_menu_keyboard())
        return
    
    drink = message.text
    price = MENU[drink]
    await OrderStates.waiting_for_quantity.set()
    await state.update_data(drink=drink, price=price)
    
    await message.answer(
        f"🥤 <b>{drink}</b>\\n"
        f"💰 <b>{price} ₽</b>\\n\\n"
        f"📝 <b>Сколько порций?</b>",
        reply_markup=get_quantity_keyboard()
    )

@dp.message_handler(state=OrderStates.waiting_for_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    logger.info(f"📊 {message.text} от {message.from_user.id}")
    
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Заказ отменён ☕", reply_markup=get_correct_keyboard())
        return
    
    try:
        qty = int(message.text[0])
        if 1 <= qty <= 5:
            data = await state.get_data()
            total = data['price'] * qty
            await state.update_data(quantity=qty, total=total)
            await OrderStates.waiting_for_confirmation.set()
            
            await message.answer(
                f"<b>📋 ПОДТВЕРДИТЕ ЗАКАЗ</b>\\n\\n"
                f"🥤 <b>{data['drink']}</b>\\n"
                f"📊 {qty} порций\\n"
                f"💰 <b>{total} ₽</b>\\n\\n"
                f"📞 <code>{CAFE_PHONE}</code>",
                reply_markup=get_confirm_keyboard()
            )
            return
    except: pass
    
    data = await state.get_data()
    await message.answer(
        f"🥤 <b>{data['drink']}</b> — {data['price']}₽\\n\\n"
        "<b>1️⃣-5️⃣</b> или <b>🔙 Отмена</b>",
        reply_markup=get_quantity_keyboard()
    )

@dp.message_handler(state=OrderStates.waiting_for_confirmation)
async def process_confirmation(message: types.Message, state: FSMContext):
    logger.info(f"✅ {message.text} от {message.from_user.id}")
    
    if "Подтвердить" in message.text:
        data = await state.get_data()
        order_data = {
            'user_id': message.from_user.id,
            'first_name': message.from_user.first_name or "Гость",
            'username': message.from_user.username or "нет",
            'drink': data['drink'],
            'quantity': data['quantity'],
            'total': data['total']
        }
        
        # ✅ АНТИСПАМ
        last_orders[message.from_user.id] = time.time()
        
        msk_time = get_moscow_time().strftime("%H:%M")
        await message.answer(
            f"🎉 <b>ЗАКАЗ #{message.from_user.id} ПРИНЯТ!</b> ☕✨\\n\\n"
            f"🕐 <i>Время MSK: {msk_time}</i>\\n"
            f"🥤 <b>{data['drink']}</b>\\n"
            f"📊 {data['quantity']} порций\\n"
            f"💰 <b>{data['total']} ₽</b>\\n\\n"
            f"📞 <code>{CAFE_PHONE}</code>",
            reply_markup=get_menu_keyboard()
        )
        
        await send_order_to_admin(order_data)
        await state.finish()
        return
    
    # ✅ ОТМЕНА → правильная клавиатура
    await state.finish()
    await message.answer("🔙 В меню ☕", reply_markup=get_correct_keyboard())

async def send_order_to_admin(order_data):
    msk_time = get_moscow_time().strftime("%H:%M")
    text = (
        f"🔔 <b>🚨 НОВЫЙ ЗАКАЗ #{order_data['user_id']}</b> ☕\\n\\n"
        f"🕐 <i>MSK: {msk_time}</i>\\n"
        f"👤 <b>{order_data['first_name']}</b> (@{order_data['username']})\\n"
        f"🆔 <code>{order_data['user_id']}</code>\\n\\n"
        f"🥤 <b>{order_data['drink']}</b>\\n"
        f"📊 <b>{order_data['quantity']} порций</b>\\n"
        f"💰 <b>{order_data['total']} ₽</b>"
    )
    try:
        await bot.send_message(ADMIN_ID, text)
        logger.info(f"✅ Заказ #{order_data['user_id']} админу OK")
    except Exception as e:
        logger.error(f"❌ Админ ошибка: {e}")

@dp.message_handler(lambda m: m.text == "📞 Позвонить")
async def call_phone(message: types.Message):
    await message.answer(
        f"📞 <b>Позвонить:</b>\\n<code>{CAFE_PHONE}</code>\\n\\n{get_work_status()}",
        reply_markup=get_correct_keyboard()
    )

@dp.message_handler(lambda m: m.text == "⏰ Часы работы")
async def work_hours(message: types.Message):
    msk_now = get_moscow_time().strftime("%H:%M")
    await message.answer(
        f"⏰ <b>{WORK_START}:00 - {WORK_END}:00 (МСК)</b>\\n\\n"
        f"🕐 Сейчас: {msk_now}\\n"
        f"{get_work_status()}\\n\\n"
        f"📞 <code>{CAFE_PHONE}</code>",
        reply_markup=get_correct_keyboard()
    )

@dp.message_handler()
async def echo(message: types.Message, state: FSMContext):
    await state.finish()
    logger.info(f"❓ Неизвестное: '{message.text}' от {message.from_user.id}")
    
    if is_cafe_open():
        await message.answer(
            f"❓ <b>{CAFE_NAME}</b>\\n\\n"
            f"{get_work_status()}\\n\\n"
            f"☕ <b>Выберите:</b>",
            reply_markup=get_menu_keyboard()
        )
    else:
        await message.answer(get_closed_message(), reply_markup=get_info_keyboard())

# ========================================
async def on_startup(dp):
    """🚀 Старт с московским временем"""
    msk_time = get_moscow_time().strftime("%H:%M")
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.sleep(1)
    await bot.set_webhook(WEBHOOK_URL)
    info = await bot.get_webhook_info()
    logger.info(f"✅ WEBHOOK: {info.url}")
    logger.info(f"🚀 v9.0 START LIVE — {CAFE_NAME} | MSK: {msk_time} | "
               f"{'🟢 ОТКРЫТО' if is_cafe_open() else '🔴 ЗАКРЫТО'}")
    logger.info("🏥 Healthcheck: CafeBotify v9.0 LIVE ✅")
    logger.info("💰 START 2990₽/мес Готов к продажам! 🚀")

async def on_shutdown(dp):
    await bot.delete_webhook()
    await dp.storage.close()
    logger.info("🛑 CafeBotify STOP")

# ========================================
if __name__ == '__main__':
    logger.info(f"🎬 v9.0 START — {CAFE_NAME} | PORT: {WEBAPP_PORT}")
    
    # ✅ RENDER HEALTHCHECK + aiogram webhook
    async def healthcheck(request):
        return web.Response(text="CafeBotify v9.0 START LIVE ✅", status=200)
    
    app = web.Application()
    app.router.add_get('/', healthcheck)
    
    executor.start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        skip_updates=True,
        host='0.0.0.0',
        port=WEBAPP_PORT,
    )
