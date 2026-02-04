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
# НАСТРОЙКИ ЛОГИРОВАНИЯ
# ========================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========================================
# ENV ПЕРЕМЕННЫЕ (Render.com)
# ========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1471275603"))
CAFE_PHONE = os.getenv("CAFE_PHONE", "+7 989 273-67-56")

# Render.com порты
PORT = int(os.environ.get("PORT", 10000))
HOST = "0.0.0.0"
WEBHOOK_URL = "https://chatbotify-2tjd.onrender.com/webhook"

# Инициализация бота
bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ========================================
# МЕНЮ КАФЕ
# ========================================
MENU = {
    "☕ Капучино": 250,
    "🥛 Латте": 270,
    "🍵 Чай": 180
}

# ========================================
# СОСТОЯНИЯ ЗАКАЗА (FSM)
# ========================================
class OrderStates(StatesGroup):
    waiting_for_quantity = State()

# ========================================
# КЛАВИАТУРЫ
# ========================================
def get_menu_keyboard():
    """Меню напитков"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    for drink in MENU.keys():
        keyboard.add(drink)
    return keyboard

def get_quantity_keyboard():
    """Клавиатура количества"""
    keyboard = types.ReplyKeyboardMarkup(
        resize_keyboard=True, 
        one_time_keyboard=True, 
        row_width=3
    )
    for i in range(1, 6):
        keyboard.add(str(i))
    keyboard.add("🔙 Отмена")
    return keyboard

def get_main_keyboard():
    """Главная клавиатура"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add("☕ Меню", "📞 Позвонить")
    return keyboard

# ========================================
# ОБРАБОТЧИКИ СООБЩЕНИЙ
# ========================================
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    """Стартовое сообщение"""
    await message.answer(
        "👋 <b>CafeBotify</b> — бот для заказа кофе!\n\n"
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
        f"✅ Вы выбрали <b>{drink}</b>\n"
        f"💰 <b>{price}₽</b> за порцию\n\n"
        f"📝 Сколько порций заказать?",
        reply_markup=get_quantity_keyboard()
    )

@dp.message_handler(state=OrderStates.waiting_for_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    """Обработка количества"""
    
    # Отмена заказа
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer(
            "❌ Заказ отменен\n\nВыберите напиток:",
            reply_markup=get_menu_keyboard()
        )
        return
    
    # Проверка числа
    try:
        quantity = int(message.text)
        if quantity <= 0 or quantity > 10:
            await message.answer("❌ Введите число от 1 до 10")
            return
            
        # Получаем данные заказа
        data = await state.get_data()
        drink = data['drink']
        price = data['price']
        total = price * quantity
        
        # Формируем заказ
        order_data = {
            'user_id': message.from_user.id,
            'username': message.from_user.username or "Не указан",
            'first_name': message.from_user.first_name or "Не указано",
            'drink': drink,
            'quantity': quantity,
            'total': total,
            'phone': CAFE_PHONE,
            'date': message.date
        }
        
        # Завершаем FSM
        await state.finish()
        
        # Отправляем админу
        await send_order_to_admin(order_data)
        
        # Подтверждение пользователю
        await message.answer(
            f"🎉 <b>Заказ принят!</b>\n\n"
            f"🥤 <b>{drink}</b>\n"
            f"📊 <b>{quantity} шт</b>\n"
            f"💰 <b>{total}₽</b>\n\n"
            f"📞 Менеджер позвонит: <b>{CAFE_PHONE}</b>\n\n"
            f"⏳ Ожидайте звонка!",
            reply_markup=get_main_keyboard()
        )
        
    except ValueError:
        await message.answer("❌ Введите число (1-10)")

@dp.message_handler(text="☕ Меню")
async def show_menu(message: types.Message):
    """Показать меню"""
    menu_text = "🍽️ <b>Меню кафе:</b>\n\n"
    for drink, price in MENU.items():
        menu_text += f"{drink} — <b>{price}₽</b>\n"
    
    await message.answer(menu_text, reply_markup=get_menu_keyboard())

@dp.message_handler(text="📞 Позвонить")
async def call_phone(message: types.Message):
    """Позвонить в кафе"""
    await message.answer(
        f"📞 Звоните в кафе: <b>{CAFE_PHONE}</b>\n\n"
        "Или напишите /start для заказа!",
        reply_markup=get_main_keyboard()
    )

@dp.message_handler()
async def unknown_message(message: types.Message):
    """Неизвестная команда"""
    await message.answer(
        "❓ Не понял команду\n\n"
        "Нажмите /start или выберите из меню:",
        reply_markup=get_menu_keyboard()
    )

# ========================================
# АДМИН ФУНКЦИИ
# ========================================
async def send_order_to_admin(order_data):
    """Отправка заказа админу"""
    message_text = (
        f"🔔 <b>НОВЫЙ ЗАКАЗ #{order_data['user_id']}</b>\n\n"
        f"👤 <b>{order_data['first_name']}</b>\n"
        f"🆔 <code>{order_data['user_id']}</code>\n"
        f"📱 @{order_data['username']}\n\n"
        f"🥤 <b>{order_data['drink']}</b>\n"
        f"📊 <b>{order_data['quantity']} шт</b>\n"
        f"💰 <b>{order_data['total']}₽</b>\n\n"
        f"📞 {order_data['phone']}"
    )
    
    try:
        await bot.send_message(ADMIN_ID, message_text)
        logger.info(f"✅ Заказ #{order_data['user_id']} отправлен админу")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки админу: {e}")

# ========================================
# WEBHOOK СЕРВЕР (Render.com)
# ========================================
async def webhook_handler(request):
    """Главный webhook обработчик"""
    try:
        # Получаем JSON от Telegram
        update = await request.json()
        logger.info(f"📨 Получен update: {update.get('update_id', 'unknown')}")
        
        # Передаем в aiogram dispatcher
        await dp.process_update(types.Update(**update))
        
        return web.json_response({"status": "ok"}, status=200)
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return web.json_response({"status": "error"}, status=500)

async def healthcheck(request):
    """Healthcheck для Render"""
    return web.Response(text="CafeBotify LIVE ✅", status=200)

async def on_startup(app):
    """Запуск сервера"""
    logger.info("🚀 НАЧИНАЕМ ЗАПУСК...")
    logger.info(f"🤖 ADMIN: {ADMIN_ID}")
    logger.info(f"📱 PHONE: {CAFE_PHONE}")
    logger.info(f"🌐 WEBHOOK: {WEBHOOK_URL}")
    
    # Очищаем старые webhooks
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("🧹 Старые webhooks удалены")
    
    # Устанавливаем новый webhook
    await bot.set_webhook(WEBHOOK_URL)
    logger.info("✅ WEBHOOK УСТАНОВЛЕН!")
    
    # Тестовое сообщение админу
    try:
        await bot.send_message(
            ADMIN_ID, 
            "🎉 <b>CafeBotify LIVE на Render.com!</b>\n\n"
            f"🌐 Webhook: {WEBHOOK_URL}\n"
            f"📱 Телефон: {CAFE_PHONE}"
        )
        logger.info("✅ Тестовое сообщение админу отправлено")
    except:
        logger.warning("⚠️ Не удалось отправить тестовое сообщение")

async def on_shutdown(app):
    """Остановка сервера"""
    logger.info("🛑 ОСТАНОВКА...")
    await bot.delete_webhook()
    await dp.storage.close()
    await bot.session.close()
    logger.info("✅ Сервер остановлен")

# ========================================
# СОЗДАНИЕ AIOHTTP ПРИЛОЖЕНИЯ
# ========================================
def create_app():
    """Создание aiohttp приложения"""
    app = web.Application()
    
    # Роуты
    app.router.add_post("/webhook", webhook_handler)
    app.router.add_get("/", healthcheck)
    
    # Startup/Shutdown хендлеры
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    return app

# ========================================
# ГЛАВНАЯ ФУНКЦИЯ ЗАПУСКА
# ========================================
if __name__ == '__main__':
    logger.info("🎬 ЗАПУСК CAFEBOTIFY...")
    
    # Создаем приложение
    app = create_app()
    
    # Запускаем на Render порту
    web.run_app(
        app,
        host=HOST,
        port=PORT,
        access_log=logger,
        access_log_format='%t "%r" %s %b "%{User-Agent}i"'
    )
