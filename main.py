import os
import json
import logging
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher.webhook import get_new_configured_app
from aiogram.utils.executor import start_webhook
from aiohttp import web
from datetime import datetime

# ========================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
                'work_hours': config.get('work_hours', [9, 21]),
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
            "work_hours": [9, 21],
            "menu": {
                "☕ Капучино": 250,
                "🥛 Латте": 270,
                "🍵 Чай": 180,
                "⚡ Эспрессо": 200
            }
        }

cafe_config = load_config()
CAFE_NAME = cafe_config["name"]
CAFE_PHONE = cafe_config["phone"]
ADMIN_ID = int(cafe_config["admin_chat_id"])
MENU = dict(cafe_config["menu"])
WORK_START = int(cafe_config["work_hours"][0])
WORK_END = int(cafe_config["work_hours"][1])

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN обязателен!")

WEBAPP_HOST = os.getenv('WEBAPP_HOST', 'chatbotify-2tjd.onrender.com')
WEBAPP_PORT = int(os.getenv('PORT', 10000))
WEBHOOK_PATH = f'/webhook/{BOT_TOKEN}'
WEBHOOK_URL = f'https://{WEBAPP_HOST}{WEBHOOK_PATH}'

# ========================================
bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

class OrderStates(StatesGroup):
    waiting_for_quantity = State()
    waiting_for_confirmation = State()

# ========================================
def is_cafe_open():
    """🕐 Проверка графика работы"""
    now = datetime.now().hour
    return WORK_START <= now < WORK_END

def get_work_status():
    """📊 Статус работы кафе"""
    now = datetime.now()
    current_hour = now.hour
    
    if is_cafe_open():
        time_left = WORK_END - current_hour
        return f"🟢 <b>Открыто</b> (ещё {time_left} ч.)"
    else:
        next_open = f"{WORK_START}:00"
        return f"🔴 <b>Закрыто</b>\n🕐 Открываемся: {next_open}"

def get_closed_notification():
    """🚫 Красивое уведомление о закрытии"""
    return (
        f"🔒 <b>{CAFE_NAME} закрыто!</b>\n\n"
        f"{get_work_status()}\n\n"
        f"📞 <b>Позвонить:</b>\n"
        f"<code>{CAFE_PHONE}</code>\n\n"
        f"☕ <i>Ждём вас в рабочее время!</i>"
    )

def get_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    for drink in MENU.keys():
        kb.add(drink)
    kb.row("📞 Позвонить", "⏰ Часы работы")
    return kb

def get_quantity_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=3)
    kb.add("1️⃣", "2️⃣", "3️⃣")
    kb.add("4️⃣", "5️⃣", "🔙 Отмена")
    return kb

def get_confirm_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    kb.add("✅ Подтвердить", "🔙 Меню")
    return kb

# ========================================
@dp.message_handler(commands=['start', 'help'])
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    logger.info(f"👤 /start от {message.from_user.id}")
    await message.answer(
        f"<b>{CAFE_NAME}</b>\n\n"
        f"🏪 {get_work_status()}\n\n"
        f"☕ <b>Выберите напиток:</b>",
        reply_markup=get_menu_keyboard()
    )

@dp.message_handler(lambda m: m.text in MENU)
async def drink_selected(message: types.Message, state: FSMContext):
    """🥤 Выбор напитка — проверка графика!"""
    logger.info(f"🥤 {message.text} от {message.from_user.id}")
    
    if not is_cafe_open():
        # 🚫 КРАСИВОЕ уведомление о закрытии
        await message.answer(
            get_closed_notification(),
            reply_markup=get_menu_keyboard()
        )
        return
        
    # ✅ Кафе открыто — продолжаем заказ
    drink = message.text
    price = MENU[drink]
    await state.finish()
    await state.update_data(drink=drink, price=price)
    await OrderStates.waiting_for_quantity.set()
    
    await message.answer(
        f"🥤 <b>{drink}</b>\n"
        f"💰 <b>{price} ₽</b>\n\n"
        f"📝 <b>Сколько порций?</b>",
        reply_markup=get_quantity_keyboard()
    )

@dp.message_handler(state=OrderStates.waiting_for_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    logger.info(f"📊 {message.text} от {message.from_user.id}")
    
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Заказ отменён ☕", reply_markup=get_menu_keyboard())
        return
    
    try:
        qty = int(message.text[0])
        if 1 <= qty <= 5:
            data = await state.get_data()
            total = data['price'] * qty
            await state.update_data(quantity=qty, total=total)
            await OrderStates.waiting_for_confirmation.set()
            
            await message.answer(
                f"<b>📋 ПОДТВЕРДИТЕ ЗАКАЗ</b>\n\n"
                f"🥤 <b>{data['drink']}</b>\n"
                f"📊 {qty} порций\n"
                f"💰 <b>{total} ₽</b>\n\n"
                f"📞 <code>{CAFE_PHONE}</code>",
                reply_markup=get_confirm_keyboard()
            )
            return
    except:
        pass
    
    data = await state.get_data()
    await message.answer(
        f"🥤 <b>{data['drink']}</b> — {data['price']}₽\n\n"
        "<b>1️⃣-5️⃣</b> или <b>🔙 Отмена</b>",
        reply_markup=get_quantity_keyboard()
    )

@dp.message_handler(state=OrderStates.waiting_for_confirmation)
async def process_confirmation(message: types.Message, state: FSMContext):
    logger.info(f"✅ {message.text} от {message.from_user.id}")
    data = await state.get_data()
    
    if message.text == "✅ Подтвердить":
        order_data = {
            'user_id': message.from_user.id,
            'first_name': message.from_user.first_name or "Гость",
            'drink': data['drink'],
            'quantity': data['quantity'],
            'total': data['total']
        }
        
        # ✅ Успешный заказ — красивое подтверждение
        await message.answer(
            f"🎉 <b>ЗАКАЗ #{message.from_user.id} ПРИНЯТ!</b> ☕✨\n\n"
            f"🥤 <b>{data['drink']}</b>\n"
            f"📊 {data['quantity']} порций\n"
            f"💰 <b>{data['total']} ₽</b>\n\n"
            f"📞 <code>{CAFE_PHONE}</code>\n"
            f"✅ <i>Готовим! ⏳</i>",
            reply_markup=get_menu_keyboard()
        )
        
        # ✅ Админу
        await send_order_to_admin(order_data)
        await state.finish()
        return
    
    await state.finish()
    await message.answer("🔙 В меню ☕", reply_markup=get_menu_keyboard())

async def send_order_to_admin(order_data):
    text = (
        f"🔔 <b>🚨 НОВЫЙ ЗАКАЗ #{order_data['user_id']}</b> ☕\n\n"
        f"👤 <b>{order_data['first_name']}</b>\n"
        f"🆔 <code>{order_data['user_id']}</code>\n"
        f"📱 <a href='tg://user?id={order_data['user_id']}'>Написать</a>\n\n"
        f"🥤 <b>{order_data['drink']}</b>\n"
        f"📊 <b>{order_data['quantity']} порций</b>\n"
        f"💰 <b>{order_data['total']} ₽</b>\n\n"
        f"📞 <code>{CAFE_PHONE}</code>"
    )
    try:
        await bot.send_message(ADMIN_ID, text)
        logger.info(f"✅ Заказ #{order_data['user_id']} админу")
    except Exception as e:
        logger.error(f"❌ Админ: {e}")

@dp.message_handler(lambda m: m.text == "📞 Позвонить")
async def call_phone(message: types.Message):
    await message.answer(
        f"📞 <b>Позвонить в {CAFE_NAME}:</b>\n\n"
        f"<code>{CAFE_PHONE}</code>\n\n"
        f"{get_work_status()}",
        reply_markup=get_menu_keyboard()
    )

@dp.message_handler(lambda m: m.text == "⏰ Часы работы")
async def work_hours(message: types.Message):
    await message.answer(
        f"⏰ <b>График работы {CAFE_NAME}</b>\n\n"
        f"🕐 <b>{WORK_START}:00 - {WORK_END}:00</b>\n\n"
        f"{get_work_status()}\n\n"
        f"📞 <code>{CAFE_PHONE}</code>",
        reply_markup=get_menu_keyboard()
    )

@dp.message_handler()
async def echo(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer(
        f"❓ <b>{CAFE_NAME}</b>\n\n"
        f"{get_work_status()}\n\n"
        f"☕ <b>Выберите:</b>",
        reply_markup=get_menu_keyboard()
    )

# ========================================
async def on_startup(app):
    webhook_info = await bot.get_webhook_info()
    if webhook_info.url != WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)
        logger.info(f"✅ Webhook установлен: {WEBHOOK_URL}")
    
    logger.info(f"🚀 v8.20 LIVE — {CAFE_NAME}")
    logger.info(f"✅ График: {WORK_START}:00-{WORK_END}:00")
    logger.info(f"📞 Админ: {ADMIN_ID}")

async def on_shutdown(app):
    await bot.delete_webhook()
    await dp.storage.close()
    await bot.session.close()
    logger.info("🛑 v8.20 STOP")

# ========================================
if __name__ == '__main__':
    logger.info(f"🎬 CAFEBOTIFY v8.20 — {CAFE_NAME}")
    
    app = get_new_configured_app(dispatcher=dp, path=WEBHOOK_PATH)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    web.run_app(app, host='0.0.0.0', port=WEBAPP_PORT)
