import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
import aiohttp
from aiohttp import web
import aioschedule as schedule
import pytz

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ENV переменные
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 1471275603))
CAFE_PHONE = os.getenv("CAFE_PHONE", "+7 989 273-67-56")

# Инициализация бота
bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Меню кафе
MENU = {
    "☕ Капучино": 250,
    "🥛 Латте": 270, 
    "🍵 Чай": 180
}

# Состояния FSM
class OrderStates(StatesGroup):
    waiting_for_drink = State()
    waiting_for_quantity = State()

PORT = int(os.environ.get("PORT", 10000))
HOST = "0.0.0.0"
WEBHOOK_URL = f"https://chatbotify-2tjd.onrender.com/webhook"
WEBAPP = None

# === ОБРАБОТЧИКИ БОТА ===
@dp.message_handler(commands=['start'])
async def start_handler(message: types.Message):
    """Стартовое сообщение с меню"""
    await message.answer(
        "👋 Добро пожаловать в CafeBotify!\n\n"
        "Выберите напиток из меню:",
        reply_markup=get_menu_keyboard()
    )

@dp.message_handler(lambda message: message.text in MENU.keys())
async def drink_selected(message: types.Message, state: FSMContext):
    """Выбор напитка"""
    drink = message.text
    price = MENU[drink]
    
    await state.update_data(drink=drink, price=price)
    await OrderStates.waiting_for_quantity.set()
    
    await message.answer(
        f"Вы выбрали <b>{drink}</b>\n💰 Цена: <b>{price}₽</b>\n\n"
        "Сколько порций хотите заказать?",
        reply_markup=get_quantity_keyboard()
    )

@dp.message_handler(state=OrderStates.waiting_for_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    """Обработка количества"""
    try:
        quantity = int(message.text)
        if quantity <= 0:
            await message.answer("❌ Количество должно быть больше 0")
            return
            
        data = await state.get_data()
        drink = data['drink']
        price = data['price']
        total = price * quantity
        
        # Сохраняем заказ
        order_data = {
            'user_id': message.from_user.id,
            'username': message.from_user.username or "Не указан",
            'first_name': message.from_user.first_name,
            'drink': drink,
            'quantity': quantity,
            'total': total,
            'phone': CAFE_PHONE
        }
        
        await state.finish()
        
        # Отправляем админу
        await send_order_to_admin(order_data, message.chat.id)
        
        # Подтверждение пользователю
        await message.answer(
            f"✅ Заказ принят!\n\n"
            f"🥤 Напиток: <b>{drink}</b>\n"
            f"📊 Количество: <b>{quantity}</b>\n"
            f"💰 Итого: <b>{total}₽</b>\n\n"
            f"📞 Позвоним для подтверждения: <b>{CAFE_PHONE}</b>\n\n"
            "Спасибо за заказ! ☕",
            reply_markup=get_main_keyboard()
        )
        
    except ValueError:
        await message.answer("❌ Введите число (например: 2)")

@dp.message_handler(commands=['menu'])
async def show_menu(message: types.Message):
    """Показать меню"""
    await message.answer(
        "🍽️ <b>Меню кафе:</b>\n\n" + 
        "\n".join([f"{k} - {v}₽" for k,v in MENU.items()]),
        reply_markup=get_menu_keyboard()
    )

# === Клавиатуры ===
def get_menu_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for drink in MENU.keys():
        keyboard.add(drink)
    return keyboard

def get_quantity_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for i in range(1, 6):
        keyboard.add(str(i))
    keyboard.add("🔙 Назад")
    return keyboard

def get_main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add("☕ Меню", "📞 Позвонить")
    return keyboard

# === АДМИН ===
async def send_order_to_admin(order_data, user_chat_id):
    """Отправка заказа админу"""
    message_text = (
        f"🔔 <b>Новый заказ!</b>\n\n"
        f"👤 Пользователь: {order_data['first_name']} (@{order_data['username']})\n"
        f"🆔 ID: <code>{order_data['user_id']}</code>\n\n"
        f"🥤 Напиток: <b>{order_data['drink']}</b>\n"
        f"📊 Количество: <b>{order_data['quantity']}</b>\n"
        f"💰 Сумма: <b>{order_data['total']}₽</b>\n\n"
        f"📞 Связаться: {order_data['phone']}"
    )
    
    await bot.send_message(ADMIN_ID, message_text)

# === WEBHOOK SERVER ===
async def on_startup(dp):
    """Запуск webhook"""
    logger.info("🚀 ЗАПУСК WEBHOOK SERVER...")
    logger.info(f"🚀 START | ADMIN: {ADMIN_ID} | PHONE: {CAFE_PHONE}")
    
    # Очищаем старые webhook
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("🧹 Старые сообщения удалены")
    
    # Устанавливаем новый webhook
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"✅ WEBHOOK: {WEBHOOK_URL}")
    
    # Тестовое сообщение админу
    try:
        await bot.send_message(ADMIN_ID, "🤖 CafeBotify LIVE на Render.com!")
        logger.info("✅ Тестовое сообщение админу отправлено")
    except:
        logger.warning("⚠️ Не удалось отправить тестовое сообщение админу")

async def on_shutdown(dp):
    """Остановка"""
    logger.info("🛑 Остановка webhook...")
    await bot.delete_webhook()
    await dp.storage.close()
    await bot.session.close()

# Aiohttp webhook handler
async def webhook_handler(request):
    """Обработчик webhook запросов от Telegram"""
    try:
        update = await request.json()
        # Отдаем диспетчеру aiogram
        await dp.process_update(types.Update(**update))
        return web.Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.Response(text="Error", status=500)

# Healthcheck для Render
async def healthcheck(request):
    """Healthcheck endpoint для Render"""
    return web.Response(text="OK", status=200)

# Создание aiohttp приложения
def create_app():
    global WEBAPP
    app = web.Application()
    
    # Роуты
    app.router.add_post('/webhook', webhook_handler)
    app.router.add_get('/', healthcheck)
    
    WEBAPP = app
    return app

# === ГЛАВНАЯ ФУНКЦИЯ ===
async def on_app_startup(app):
    """Старт всего приложения"""
    await on_startup(dp)
    logger.info(f"✅ Сервер запущен на {HOST}:{PORT}")

if __name__ == '__main__':
    # Создаем aiohttp app
    app = create_app()
    
    # Запускаем на правильном порту для Render
    web.run_app(
        app, 
        host=HOST, 
        port=PORT,
        access_log=logger
    )
