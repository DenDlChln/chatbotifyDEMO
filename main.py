import os
import json
import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher.webhook import get_new_configured_app
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
WEBAPP_HOST = os.getenv('WEBAPP_HOST', 'chatbotify-2tjd.onrender.com')
WEBAPP_PORT = int(os.getenv('PORT', 10000))
WEBHOOK_PATH = f'/webhook/{BOT_TOKEN}'

# ========================================
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

class OrderStates(StatesGroup):
    waiting_for_quantity = State()
    waiting_for_confirmation = State()

# ========================================
def is_cafe_open():
    now = datetime.now().hour
    return WORK_START <= now < WORK_END

def get_work_status():
    now = datetime.now()
    current_hour = now.hour
    if is_cafe_open():
        time_left = WORK_END - current_hour
        return f"🟢 <b>Открыто</b> (ещё {time_left} ч.)"
    else:
        next_open = f"{WORK_START}:00"
        return f"🔴 <b>Закрыто</b>\n🕐 Открываемся: {next_open}"

def get_closed_notification():
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
        reply_markup=get_menu_keyboard(),
        parse_mode='HTML'
    )

@dp.message_handler(lambda m: m.text in MENU)
async def drink_selected(message: types.Message, state: FSMContext):
    logger.info(f"🥤 {message.text} от {message.from_user.id}")
    
    if not is_cafe_open():
        await message.answer(
            get_closed_notification(),
            reply_markup=get_menu_keyboard(),
            parse_mode='HTML'
        )
        return
        
    drink = message.text
    price = MENU[drink]
    await OrderStates.waiting_for_quantity.set()
    await state.update_data(drink=drink, price=price)
    
    await message.answer(
        f"🥤 <b>{drink}</b>\n"
        f"💰 <b>{price} ₽</b>\n\n"
        f"📝 <b>Сколько порций?</b>",
        reply_markup=get_quantity_keyboard(),
        parse_mode='HTML'
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
                reply_markup=get_confirm_keyboard(),
                parse_mode='HTML'
            )
            logger.info(f"📋 Подтверждение от {message.from_user.id}")
            return
    except:
        pass
    
    data = await state.get_data()
    await message.answer(
        f"🥤 <b>{data['drink']}</b> — {data['price']}₽\n\n"
        "<b>1️⃣-5️⃣</b> или <b>🔙 Отмена</b>",
        reply_markup=get_quantity_keyboard(),
        parse_mode='HTML'
    )

@dp.message_handler(state=OrderStates.waiting_for_confirmation)
async def process_confirmation(message: types.Message, state: FSMContext):
    logger.info(f"✅ {message.text} от {message.from_user.id}")
    
    data = await state.get_data()
    
    if "Подтвердить" in message.text:
        order_data = {
            'user_id': message.from_user.id,
            'first_name': message.from_user.first_name or "Гость",
            'drink': data['drink'],
            'quantity': data['quantity'],
            'total': data['total']
        }
        
        await message.answer(
            f"🎉 <b>ЗАКАЗ #{message.from_user.id} ПРИНЯТ!</b> ☕✨\n\n"
            f"🥤 <b>{data['drink']}</b>\n"
            f"📊 {data['quantity']} порций\n"
            f"💰 <b>{data['total']} ₽</b>\n\n"
            f"📞 <code>{CAFE_PHONE}</code>\n"
            f"✅ <i>Готовим! ⏳</i>",
            reply_markup=get_menu_keyboard(),
            parse_mode='HTML'
        )
        
        await send_order_to_admin(order_data)
        await state.finish()
        return
    
    await state.finish()
    await message.answer("🔙 В меню ☕", reply_markup=get_menu_keyboard())

async def send_order_to_admin(order_data):
    text = (
        f"🔔 <b>🚨 НОВЫЙ ЗАКАЗ #{order_data['user_id']}</b> ☕\n\n"
        f"👤 <b>{order_data['first_name']}</b>\n"
        f"🆔 <code>{order_data['user_id']}</code>\n\n"
        f"🥤 <b>{order_data['drink']}</b>\n"
        f"📊 <b>{order_data['quantity']} порций</b>\n"
        f"💰 <b>{order_data['total']} ₽</b>"
    )
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode='HTML')
        logger.info(f"✅ Заказ #{order_data['user_id']} админу")
    except Exception as e:
        logger.error(f"❌ Админ: {e}")

@dp.message_handler(lambda m: m.text in ["📞 Позвонить", "⏰ Часы работы"])
async def cafe_info(message: types.Message):
    if "📞" in message.text:
        await message.answer(
            f"📞 <b>Позвонить:</b>\n<code>{CAFE_PHONE}</code>\n\n{get_work_status()}",
            reply_markup=get_menu_keyboard(),
            parse_mode='HTML'
        )
    else:
        await message.answer(
            f"⏰ <b>{WORK_START}:00 - {WORK_END}:00</b>\n\n{get_work_status()}\n\n📞 <code>{CAFE_PHONE}</code>",
            reply_markup=get_menu_keyboard(),
            parse_mode='HTML'
        )

@dp.message_handler()
async def echo(message: types.Message, state: FSMContext):
    await state.finish()
    logger.info(f"❓ Неизвестное: {message.text} от {message.from_user.id}")
    await message.answer(
        f"❓ <b>{CAFE_NAME}</b>\n\n"
        f"{get_work_status()}\n\n"
        f"☕ <b>Выберите:</b>",
        reply_markup=get_menu_keyboard(),
        parse_mode='HTML'
    )

# ========================================
async def on_startup(dp):
    webhook_url = f"https://{WEBAPP_HOST}{WEBHOOK_PATH}"
    await bot.set_webhook(webhook_url)
    logger.info(f"✅ Webhook: {webhook_url}")
    logger.info(f"🚀 v8.21 LIVE — {CAFE_NAME}")

async def on_shutdown(dp):
    await bot.delete_webhook()
    logger.info("🛑 v8.21 STOP")

# ========================================
async def main():
    logger.info(f"🎬 v8.21 WEBHOOK — {CAFE_NAME}")
    
    app = web.Application()
    app.on_startup.append(lambda _: on_startup(dp))
    app.on_shutdown.append(lambda _: on_shutdown(dp))
    
    # ✅ ПРАВИЛЬНАЯ регистрация webhook handlers
    app.router.add_post(WEBHOOK_PATH, get_new_configured_app(dispatcher=dp, path=WEBHOOK_PATH))
    
    # ✅ Healthcheck для Render
    async def healthcheck(request):
        return web.Response(text="v8.21 LIVE", status=200)
    
    app.router.add_get('/', healthcheck)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', WEBAPP_PORT)
    await site.start()
    logger.info(f"🌐 Server на 0.0.0.0:{WEBAPP_PORT}")

if __name__ == '__main__':
    asyncio.run(main())
