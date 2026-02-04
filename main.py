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
def load_config():
    """🔒 100% безопасная загрузка config.json"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            config = data.get('cafe', {})
            cafe_config = {
                'name': config.get('name', 'Кофейня «Уют» ☕'),
                'phone': config.get('phone', '+7 989 273-67-56'),
                'admin_chat_id': config.get('admin_chat_id', 1471275603),
                'work_hours': config.get('work_hours', [9, 21]),
                'menu': config.get('menu', {
                    "☕ Капучино": 250,
                    "🥛 Латте": 270,
                    "🍵 Чай": 180,
                    "⚡ Эспрессо": 200,
                    "☕ Американо": 300
                })
            }
            logger.info(f"✅ config.json: {cafe_config['name']}")
            return cafe_config
    except Exception as e:
        logger.warning(f"⚠️ config.json: {e} — дефолт")
        return {
            "name": "Кофейня «Уют» ☕",
            "phone": "+7 989 273-67-56",
            "admin_chat_id": 1471275603,
            "work_hours": [9, 21],
            "menu": {
                "☕ Капучино": 250,
                "🥛 Латте": 270,
                "🍵 Чай": 180
            }
        }

cafe_config = load_config()
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
    raise ValueError("❌ BOT_TOKEN обязателен!")

logger.info(f"🚀 v8.12 POLLING — {CAFE_NAME} | {len(MENU)} позиций")

# ========================================
bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

class OrderStates(StatesGroup):
    waiting_for_quantity = State()
    waiting_for_confirmation = State()

# ========================================
def get_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    for drink in list(MENU.keys())[:6]:
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
        return f"🟢 <b>Открыто</b> (до {WORK_END_HOUR}:00)"
    return f"🔴 <b>Закрыто</b>\n🕐 {WORK_START_HOUR}:00-{WORK_END_HOUR}:00"

# ========================================
@dp.message_handler(commands=['start', 'help'])
async def cmd_start(message: types.Message, state: FSMContext):
    logger.info(f"👤 /start от {message.from_user.id}")
    await state.finish()
    await message.answer(
        f"<b>{CAFE_NAME}</b>\n\n"
        f"🏪 {get_work_status()}\n\n"
        f"<b>☕ Выберите напиток:</b>",
        reply_markup=get_menu_keyboard()
    )

@dp.message_handler(lambda m: m.text in MENU)
async def drink_selected(message: types.Message, state: FSMContext):
    logger.info(f"🥤 {message.text} от {message.from_user.id}")
    await state.finish()
    
    if not is_cafe_open():
        await message.answer(
            f"🔴 <b>{CAFE_NAME} закрыто!</b>\n\n"
            f"📞 <code>{CAFE_PHONE}</code>",
            reply_markup=get_main_keyboard()
        )
        return
    
    drink = message.text
    price = MENU[drink]
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
                "💰 <b>{total} ₽</b>\n\n"
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
        await send_order_to_admin(order_data)
        
        await state.finish()
        await message.answer(
            f"🎉 <b>ЗАКАЗ #{message.from_user.id} ПРИНЯТ!</b>\n\n"
            f"🥤 {data['drink']}\n"
            f"📊 {data['quantity']} порций\n"
            f"💰 <b>{data['total']} ₽</b>\n\n"
            f"📞 <code>{CAFE_PHONE}</code>\n"
            f"✅ <i>Готовим! ⏳</i>",
            reply_markup=get_main_keyboard()
        )
        return
        
    elif message.text == "🔙 Меню":
        await state.finish()
        await message.answer("🔙 В меню ☕", reply_markup=get_menu_keyboard())
        return
        
    await message.answer(
        f"<b>📋 {data['drink']} ×{data['quantity']} = {data['total']}₽</b>\n\n"
        "<b>✅ Подтвердить</b> / <b>🔙 Меню</b>",
        reply_markup=get_confirm_keyboard()
    )

@dp.message_handler(text=["☕ Меню", "📞 Позвонить", "⏰ Часы работы", "ℹ️ О боте"])
async def menu_actions(message: types.Message, state: FSMContext):
    await state.finish()
    
    if "📞" in message.text:
        await message.answer(
            f"📞 <b>{CAFE_NAME}</b>\n<code>{CAFE_PHONE}</code>",
            reply_markup=get_menu_keyboard()
        )
    elif "⏰" in message.text:
        await message.answer(
            f"🕐 <b>{CAFE_NAME}</b>\n"
            f"🟢 {WORK_START_HOUR}:00-{WORK_END_HOUR}:00\n"
            f"{get_work_status()}",
            reply_markup=get_menu_keyboard()
        )
    elif "О боте" in message.text:
        await message.answer(
            f"🤖 <b>CAFEBOTIFY v8.12 POLLING</b>\n\n"
            f"✅ Render.com LIVE\n"
            f"✅ aiogram 2.25.1\n"
            f"✅ Заказы 24/7",
            reply_markup=get_main_keyboard()
        )
    else:
        menu_text = f"🍽️ <b>{CAFE_NAME}</b>\n\n"
        for drink, price in MENU.items():
            menu_text += f"{drink} — <b>{price}₽</b>\n"
        await message.answer(menu_text, reply_markup=get_menu_keyboard())

@dp.message_handler()
async def unknown(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer(
        f"❓ <b>{CAFE_NAME}</b>\n\n{get_work_status()}\n☕ Выберите:",
        reply_markup=get_menu_keyboard()
    )

# ========================================
async def send_order_to_admin(order_data):
    text = (
        f"🔔 <b>🚨 НОВЫЙ ЗАКАЗ #{order_data['user_id']}</b>\n\n"
        f"👤 <b>{order_data['first_name']}</b>\n"
        f"🆔 <code>{order_data['user_id']}</code>\n\n"
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

# ========================================
async def on_startup(dp):
    logger.info(f"🚀 v8.12 POLLING LIVE — {CAFE_NAME}")
    logger.info(f"✅ Бот: CafeBotify")
    logger.info(f"📞 Админ: {ADMIN_ID}")

async def on_shutdown(dp):
    logger.info("🛑 v8.12 STOP")

# ========================================
if __name__ == '__main__':
    logger.info(f"🎬 CAFEBOTIFY v8.12 POLLING MODE")
    executor.start_polling(
        dispatcher=dp,
        skip_updates=True,
        on_startup=on_startup,
        on_shutdown=on_shutdown
    )
