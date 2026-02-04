import os
import json
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from datetime import datetime, time

# ========================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========================================
# ✅ ЧИТАЕМ ВАШ config.json
def load_config():
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data['cafe']
    except FileNotFoundError:
        logger.error("❌ config.json не найден!")
        return None
    except (KeyError, json.JSONDecodeError) as e:
        logger.error(f"❌ Ошибка config.json: {e}")
        return None

cafe_config = load_config()
if not cafe_config:
    # ✅ Дефолтные значения для безопасности
    cafe_config = {
        "name": "Кофейня ☕",
        "phone": "+7 989 273-67-56",
        "admin_chat_id": 1471275603,
        "work_hours": [9, 21],
        "menu": {"☕ Капучино": 250}
    }
    logger.warning("⚠️ Используем дефолтный config")

# ✅ ПЕРЕМЕННЫЕ ИЗ CONFIG
CAFE_NAME = cafe_config["name"]
CAFE_PHONE = cafe_config["phone"]
ADMIN_ID = int(cafe_config["admin_chat_id"])
MENU = dict(cafe_config["menu"])
WORK_START_HOUR = int(cafe_config["work_hours"][0])
WORK_END_HOUR = int(cafe_config["work_hours"][1])

WORK_START = time(WORK_START_HOUR, 0)
WORK_END = time(WORK_END_HOUR, 0)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise Exception("❌ BOT_TOKEN обязателен в Environment!")

PORT = int(os.environ.get("PORT", 10000))
HOST = "0.0.0.0"
WEBHOOK_PATH = "/webhook"

# ========================================
bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

class OrderStates(StatesGroup):
    waiting_for_quantity = State()

# ========================================
def get_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    for drink in list(MENU.keys())[:6]:  # Максимум 6 кнопок в столбец
        kb.add(drink)
    kb.row("📞 Позвонить", "⏰ Часы работы")
    return kb

def get_quantity_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=3)
    kb.add("1️⃣", "2️⃣", "3️⃣")
    kb.add("4️⃣", "5️⃣", "🔙 Отмена")
    return kb

def get_main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.row("☕ Меню", "📞 Позвонить")
    kb.row("⏰ Часы работы", "ℹ️ О боте")
    return kb

# ========================================
def is_cafe_open():
    now = datetime.now().time()
    return WORK_START <= now <= WORK_END

def get_work_status():
    if is_cafe_open():
        return f"🟢 <b>Открыто сейчас</b> (до {WORK_END_HOUR}:00)"
    return f"🔴 <b>Закрыто</b>\n🕐 с {WORK_START_HOUR}:00 до {WORK_END_HOUR}:00"

# ========================================
@dp.message_handler(commands=['start', 'help'])
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    status = get_work_status()
    
    welcome_text = (
        f"{CAFE_NAME}\n\n"
        f"🏪 {status}\n\n"
        "<b>☕ Выберите напиток или действие ниже 😊</b>"
    )
    
    await message.answer(welcome_text, reply_markup=get_menu_keyboard())
    logger.info(f"👤 /start от {message.from_user.id}")

@dp.message_handler(lambda m: m.text in MENU)
async def drink_selected(message: types.Message, state: FSMContext):
    if not is_cafe_open():
        await message.answer(
            f"🔴 <b>{CAFE_NAME} закрыто!</b>\n\n"
            f"📞 {CAFE_PHONE}\n{get_work_status()}",
            reply_markup=get_main_keyboard()
        )
        return
    
    await state.finish()
    drink = message.text
    price = MENU[drink]
    
    await state.update_data(drink=drink, price=price)
    await OrderStates.waiting_for_quantity.set()
    
    await message.answer(
        f"{drink}\n💰 <b>{price} ₽</b>\n\n"
        f"📝 <b>Сколько порций?</b>",
        reply_markup=get_quantity_keyboard()
    )

@dp.message_handler(state=OrderStates.waiting_for_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Заказ отменён", reply_markup=get_menu_keyboard())
        return
    
    try:
        qty = int(message.text[0])
        if 1 <= qty <= 5:
            data = await state.get_data()
            total = data['price'] * qty
            
            await state.finish()
            await send_order_to_admin({
                'user_id': message.from_user.id,
                'first_name': message.from_user.first_name or "Гость",
                'username': message.from_user.username or "",
                'drink': data['drink'],
                'quantity': qty,
                'total': total
            })
            
            await message.answer(
                f"🎉 <b>Заказ #{message.from_user.id}</b>\n\n"
                f"{data['drink']}\n📊 <b>{qty} порций</b>\n"
                f"💰 <b>{total} ₽</b>\n\n📞 {CAFE_PHONE}\n✅ Готовим!",
                reply_markup=get_main_keyboard()
            )
            return
    except:
        pass
    
    data = await state.get_data()
    await message.answer(
        f"{data['drink']}\n💰 <b>{data['price']} ₽</b>\n\n"
        "❌ <b>1️⃣-5️⃣</b> или <b>🔙 Отмена</b>",
        reply_markup=get_quantity_keyboard()
    )

@dp.message_handler(text=["☕ Меню", "📞 Позвонить", "⏰ Часы работы", "ℹ️ О боте"])
async def menu_actions(message: types.Message, state: FSMContext):
    await state.finish()
    
    if "📞" in message.text:
        await message.answer(
            f"📞 <b>{CAFE_NAME}:</b>\n<code>{CAFE_PHONE}</code>\n\nЗакажите ☕:",
            reply_markup=get_menu_keyboard()
        )
    elif "⏰" in message.text:
        await message.answer(
            f"🕐 <b>{CAFE_NAME}:</b>\n🟢 {WORK_START_HOUR}:00 - {WORK_END_HOUR}:00\n\n{get_work_status()}",
            reply_markup=get_menu_keyboard()
        )
    elif "О боте" in message.text:
        await message.answer(
            f"🤖 <b>CAFEBOTIFY — 2990₽/мес</b>\n\n✅ Меню в Telegram\n✅ Заказы 24/7\n✅ Уведомления вам\n✅ Автоответ ночью\n\n🎯 {CAFE_NAME}",
            reply_markup=get_main_keyboard()
        )
    else:
        menu_text = f"🍽️ <b>{CAFE_NAME}:</b>\n\n" + "\n".join(f"{k} — <b>{v}₽</b>" for k,v in MENU.items())
        await message.answer(menu_text, reply_markup=get_menu_keyboard())

@dp.message_handler()
async def unknown(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer(
        f"❓ <b>{CAFE_NAME}</b>\n\n{get_work_status()}",
        reply_markup=get_menu_keyboard()
    )

# ========================================
async def send_order_to_admin(order_data):
    text = (
        f"🔔 <b>🚨 ЗАКАЗ #{order_data['user_id']} | {CAFE_NAME}</b>\n\n"
        f"👤 {order_data['first_name']}\n🆔 <code>{order_data['user_id']}</code>\n"
        f"📱 <a href='tg://user?id={order_data['user_id']}'>Написать</a>\n\n"
        f"🥤 <b>{order_data['drink']}</b>\n📊 <b>{order_data['quantity']}x</b>\n"
        f"💰 <b>{order_data['total']}₽</b>\n📞 {CAFE_PHONE}"
    )
    try:
        await bot.send_message(ADMIN_ID, text)
        logger.info("✅ Админ уведомлён")
    except Exception as e:
        logger.error(f"❌ Админ: {e}")

# ========================================
# ✅ ФИКС NoneType: АСИНХРОННЫЕ startup/shutdown
async def on_startup(dp):
    logger.info(f"🚀 CAFEBOTIFY v8.2 — {CAFE_NAME}")
    logger.info(f"☕ Меню: {len(MENU)} позиций")
    logger.info(f"📞 {CAFE_PHONE}")

async def on_shutdown(dp):
    logger.info("🛑 Остановка")

# ========================================
if __name__ == '__main__':
    executor.start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,     # ✅ АСИНХРОННАЯ!
        on_shutdown=on_shutdown,   # ✅ АСИНХРОННАЯ!
        skip_updates=True,
        host=HOST,
        port=PORT,
    )
