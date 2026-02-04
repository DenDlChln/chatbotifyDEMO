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
logging.basicConfig(level=logging.DEBUG)  # ✅ DEBUG для диагностики
logger = logging.getLogger(__name__)

# ========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1471275603"))
CAFE_PHONE = os.getenv("CAFE_PHONE", "+7 989 273-67-56")
PORT = int(os.environ.get("PORT", 10000))
HOST = "0.0.0.0"

# ✅ ДИНАМИЧЕСКИЙ WEBHOOK URL
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"  # Безопаснее
WEBHOOK_URL = f"https://chatbotify-2tjd.onrender.com{WEBHOOK_PATH}"

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
# КЛАВИАТУРЫ (упрощено)
def get_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    kb.add("☕ Капучино", "🥛 Латте", "🍵 Чай", "📞 Позвонить")
    return kb

def get_quantity_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("1", "2", "3", "4", "5", "🔙 Отмена")
    return kb

# ========================================
@dp.message_handler(commands=['start', 'help'])
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    logger.info(f"👤 /start от {message.from_user.id}")
    await message.answer("🎉 CAFEBOTIFY v6.3!\nВыберите ☕:", reply_markup=get_menu_keyboard())

@dp.message_handler(lambda m: m.text in MENU)
async def drink_selected(message: types.Message, state: FSMContext):
    await state.finish()
    drink = message.text
    price = MENU[drink]
    await state.update_data(drink=drink, price=price)
    await OrderStates.waiting_for_quantity.set()
    await message.answer(f"✅ {drink} ({price}₽)\nСколько?", reply_markup=get_quantity_keyboard())

@dp.message_handler(state=OrderStates.waiting_for_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        return await message.answer("❌ Отменено", reply_markup=get_menu_keyboard())
    
    try:
        qty = int(message.text)
        if 1 <= qty <= 10:
            data = await state.get_data()
            total = data['price'] * qty
            await state.finish()
            
            await send_order_to_admin({
                'user_id': message.from_user.id,
                'drink': data['drink'],
                'quantity': qty,
                'total': total
            })
            
            await message.answer(f"✅ Заказ {total}₽\n📞 {CAFE_PHONE}")
            return
    except:
        pass
    
    await message.answer("❌ 1-10 или Отмена", reply_markup=get_quantity_keyboard())

@dp.message_handler()
async def echo(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("/start", reply_markup=get_menu_keyboard())

# ========================================
async def send_order_to_admin(data):
    try:
        await bot.send_message(ADMIN_ID, f"🔔 Заказ #{data['user_id']}: {data['drink']} x{data['quantity']} = {data['total']}₽")
    except:
        pass

# ========================================
# ✅ WEBHOOK С ПОЛНОЙ ДИАГНОСТИКОЙ
async def webhook_handler(request):
    logger.info(f"🌐 {request.method} {request.path} from {request.remote}")
    
    try:
        # ✅ ЛОГИРУЕМ ВСЕ HEADERS
        logger.info(f"📋 Headers: {dict(request.headers)}")
        
        # ✅ Проверяем content-type
        content_type = request.headers.get('content-type', '')
        logger.info(f"📄 Content-Type: {content_type}")
        
        if content_type != 'application/json':
            logger.warning(f"❌ Неправильный content-type: {content_type}")
            return web.Response(text="OK", status=200)
        
        update = await request.json()
        logger.info(f"📨 Update #{update.get('update_id', 'NO_ID')}")
        
        Bot.set_current(bot)
        Dispatcher.set_current(dp)
        await dp.process_update(types.Update(**update))
        
        logger.info("✅ ОБРАБОТАНО")
        return web.Response(text="OK", status=200)
        
    except Exception as e:
        logger.error(f"💥 ОШИБКА WEBHOOK: {e}", exc_info=True)
        return web.Response(text="ERROR", status=500)

# ========================================
async def healthcheck(request):
    return web.Response(text=f"LIVE v6.3 | Port:{PORT} | Webhook:{WEBHOOK_URL}", status=200)

async def on_startup(app):
    logger.info("🚀 ЗАПУСК v6.3")
    logger.info(f"🔑 TOKEN: {BOT_TOKEN[:10]}...")
    logger.info(f"👑 ADMIN: {ADMIN_ID}")
    logger.info(f"🌐 WEBHOOK: {WEBHOOK_URL}")
    
    # ✅ ОЧИСТКА + НОВЫЙ WEBHOOK
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.sleep(2)
    await bot.set_webhook(WEBHOOK_URL)
    
    info = await bot.get_webhook_info()
    logger.info(f"✅ WEBHOOK INFO: {info}")
    
    await bot.send_message(ADMIN_ID, f"🔥 v6.3 LIVE!\n{WEBHOOK_URL}\nТест: /start")

async def on_shutdown(app):
    await bot.delete_webhook()
    await dp.storage.close()

# ========================================
def create_app():
    app = web.Application()
    app.router.add_post("/webhook", webhook_handler)  # ✅ УБРАЛИ TOKEN из пути
    app.router.add_get("/", healthcheck)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app

if __name__ == '__main__':
    logger.info("🎬 v6.3 - WEBHOOK DIAGNOSTICS")
    app = create_app()
    web.run_app(app, host=HOST, port=PORT)
