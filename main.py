import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
import aiohttp
from aiohttp import web

# ========================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1471275603"))
CAFE_PHONE = os.getenv("CAFE_PHONE", "+7 989 273-67-56")
PORT = int(os.environ.get("PORT", 10000))
HOST = "0.0.0.0"
WEBHOOK_URL = "https://chatbotify-2tjd.onrender.com/webhook"

bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

MENU = {
    "☕ Капучино": 250,
    "🥛 Латте": 270,
    "🍵 Чай": 180
}

class OrderStates(StatesGroup):
    waiting_for_quantity = State()

# ========================================
def get_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    kb.add("☕ Капучино")
    kb.add("🥛 Латте")
    kb.add("🍵 Чай")
    kb.add("📞 Позвонить")
    return kb

def get_quantity_keyboard():
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True, one_time_keyboard=True, row_width=3
    )
    kb.add("1", "2", "3")
    kb.add("4", "5", "🔙 Отмена")
    return kb

def get_main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("☕ Меню", "📞 Позвонить")
    return kb

# ========================================
@dp.message_handler(commands=['start', 'help'])
async def cmd_start(message: types.Message, state: FSMContext):
    # ✅ КРИТИЧНО: сбрасываем ВСЕ состояния при /start
    await state.finish()
    logger.info(f"👤 /start от {message.from_user.id}")
    await message.answer(
        "🎉 <b>CAFEBOTIFY v6.2 LIVE!</b>\n\n"
        "👋 Выберите напиток:",
        reply_markup=get_menu_keyboard()
    )

@dp.message_handler(lambda m: m.text in MENU)
async def drink_selected(message: types.Message, state: FSMContext):
    logger.info(f"🥤 {message.text}")
    
    # ✅ Очищаем старое состояние перед новым
    await state.finish()
    
    drink = message.text
    price = MENU[drink]
    
    await state.update_data(drink=drink, price=price)
    await OrderStates.waiting_for_quantity.set()
    
    await message.answer(
        f"✅ <b>{drink}</b>\n💰 <b>{price}₽</b>\n\n"
        "📝 Сколько порций?",
        reply_markup=get_quantity_keyboard()
    )

@dp.message_handler(state=OrderStates.waiting_for_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    logger.info(f"📊 {message.text}")
    
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Отменено", reply_markup=get_menu_keyboard())
        return
    
    try:
        qty = int(message.text)
        if 1 <= qty <= 10:
            data = await state.get_data()
            total = data['price'] * qty
            
            # ✅ ПОЛНАЯ ОЧИСТКА СОСТОЯНИЯ
            await state.finish()
            
            order_data = {
                'user_id': message.from_user.id,
                'first_name': message.from_user.first_name or "",
                'username': message.from_user.username or "",
                'drink': data['drink'],
                'quantity': qty,
                'total': total,
                'phone': CAFE_PHONE
            }
            
            await send_order_to_admin(order_data)
            
            await message.answer(
                f"✅ <b>ЗАКАЗ #{message.from_user.id}</b>\n\n"
                f"🥤 {data['drink']}\n📊 {qty} шт\n💰 <b>{total}₽</b>\n\n"
                f"📞 {CAFE_PHONE}",
                reply_markup=get_main_keyboard()
            )
            logger.info(f"✅ Заказ {total}₽")
            return
    except ValueError:
        pass
    
    # ❌ Повторяем запрос
    data = await state.get_data()
    await message.answer(
        f"✅ <b>{data['drink']}</b>\n💰 <b>{data['price']}₽</b>\n\n"
        "❌ Введите 1-10 или <b>🔙 Отмена</b>",
        reply_markup=get_quantity_keyboard()
    )

# ========================================
@dp.message_handler(text=["☕ Меню", "📞 Позвонить"])
async def menu_phone(message: types.Message, state: FSMContext):
    # ✅ СБРАСЫВАЕМ СОСТОЯНИЕ при переходе в меню
    await state.finish()
    
    if message.text == "📞 Позвонить":
        await message.answer(f"📞 <b>{CAFE_PHONE}</b>", reply_markup=get_menu_keyboard())
    else:
        text = "🍽️ <b>Меню:</b>\n\n" + "\n".join(f"{k} — <b>{v}₽</b>" for k,v in MENU.items())
        await message.answer(text, reply_markup=get_menu_keyboard())

# ========================================
@dp.message_handler()
async def echo(message: types.Message, state: FSMContext):
    # ✅ СБРАСЫВАЕМ ЛЮБОЕ СОСТОЯНИЕ
    await state.finish()
    await message.answer("👋 /start", reply_markup=get_menu_keyboard())

# ========================================
async def send_order_to_admin(data):
    text = (
        f"🔔 <b>ЗАКАЗ #{data['user_id']}</b>\n\n"
        f"👤 {data['first_name']} (@{data['username']})\n"
        f"🥤 <b>{data['drink']}</b>\n📊 <b>{data['quantity']}x</b>\n"
        f"💰 <b>{data['total']}₽</b>\n📞 {data['phone']}"
    )
    try:
        await bot.send_message(ADMIN_ID, text)
    except:
        pass

# ========================================
async def webhook_handler(request):
    try:
        logger.info("🔥 WEBHOOK")
        update = await request.json()
        logger.info(f"📨 #{update.get('update_id')}")
        
        Bot.set_current(bot)
        Dispatcher.set_current(dp)
        await dp.process_update(types.Update(**update))
        
        logger.info("✅ OK")
        return web.Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"💥 {e}")
        return web.Response(text="ERROR", status=500)

async def healthcheck(request):
    return web.Response(text="LIVE v6.2 ✅", status=200)

# ========================================
async def on_startup(app):
    logger.info("🚀 v6.2 START")
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)
    await bot.send_message(ADMIN_ID, "🔥 v6.2 LIVE! Тест: /start → ☕ → 2")

async def on_shutdown(app):
    await bot.delete_webhook()
    await dp.storage.close()

# ========================================
def create_app():
    app = web.Application()
    app.router.add_post("/webhook", webhook_handler)
    app.router.add_get("/", healthcheck)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app

if __name__ == '__main__':
    logger.info("🎬 v6.2 - FSM FIXED!")
    app = create_app()
    web.run_app(app, host=HOST, port=PORT)
