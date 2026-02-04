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
logging.basicConfig(level=logging.DEBUG)
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
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("1", "2", "3", "4", "5", "🔙 Отмена")
    return kb

# ========================================
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("🎉 CAFEBOTIFY v6.5!\nВыберите ☕:", reply_markup=get_menu_keyboard())

@dp.message_handler(lambda m: m.text in MENU)
async def drink_selected(message: types.Message, state: FSMContext):
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
            await message.answer(f"✅ Заказ {total}₽\n📞 {CAFE_PHONE}")
            return
    except:
        pass
    await message.answer("❌ 1-10", reply_markup=get_quantity_keyboard())

@dp.message_handler()
async def echo(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("/start", reply_markup=get_menu_keyboard())

# ========================================
# ✅ ЛОВИМ ВСЕ POST ЗАПРОСЫ!
async def webhook_handler(request):
    logger.info(f"🌐 POST {request.path} <- {request.remote}")
    
    # ✅ ЛОВИМ ЛЮБОЙ POST (даже с токеном в пути)
    if request.method == 'POST':
        try:
            # Пробуем JSON
            update = await request.json()
            logger.info(f"📨 JSON Update #{update.get('update_id', 'unknown')}")
            
            Bot.set_current(bot)
            Dispatcher.set_current(dp)
            await dp.process_update(types.Update(**update))
            
            logger.info("✅ JSON OK")
            return web.Response(text="OK", status=200)
            
        except aiohttp.ContentTypeError:
            # Пробуем form-data (Telegram иногда шлет так)
            logger.info("📨 Form-data detected")
            data = await request.post()
            logger.warning(f"Form-data: {data}")
            
        except Exception as e:
            logger.error(f"💥 {e}")
    
    return web.Response(text="OK", status=200)

# ✅ ЛОВИМ ВСЕ POST ПУТИ (с токеном тоже!)
async def catch_all_post(request):
    logger.info(f"🎣 CATCH POST {request.path}")
    return await webhook_handler(request)

async def healthcheck(request):
    return web.Response(text="v6.5 LIVE | /webhook OK", status=200)

# ========================================
async def on_startup(app):
    logger.info("🚀 v6.5 START")
    logger.info(f"WEBHOOK: {WEBHOOK_URL}")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.sleep(2)
    await bot.set_webhook(WEBHOOK_URL)
    
    info = await bot.get_webhook_info()
    logger.info(f"SET: {info.url}")
    
    await bot.send_message(ADMIN_ID, "🔥 v6.5 LIVE!\nПиши /start")

async def on_shutdown(app):
    await bot.delete_webhook()
    await dp.storage.close()

# ========================================
def create_app():
    app = web.Application()
    
    # ✅ ЛОВИМ ВСЕ POST ПУТИ!
    app.router.add_post("/webhook", webhook_handler)
    app.router.add_post("/webhook/{token:.+}", catch_all_post)  # С токеном тоже!
    
    app.router.add_get("/", healthcheck)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app

if __name__ == '__main__':
    logger.info("🎬 v6.5 - CATCH ALL POST!")
    app = create_app()
    web.run_app(app, host=HOST, port=PORT)
