import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils import executor
from aiogram.utils.exceptions import CantParseEntities

# 🛠️ ЛОГИ
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 🔥 ENV (с fallback для теста)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1471275603"))
CAFE_PHONE = os.getenv("CAFE_PHONE", "+7 989 273-67-56")

# ✅ ПРОВЕРКА
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN обязателен! Render → Environment")
    exit(1)

logger.info(f"🚀 START | ADMIN: {ADMIN_ID} | PHONE: {CAFE_PHONE}")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# 🍽️ ПОЛНОЕ МЕНЮ
CAFE_MENU = {
    "☕ Капучино": 250,
    "🥛 Латте": 270,
    "🍵 Чай": 180,
    "⚡ Эспрессо": 200,
    "☕ Американо": 300,
    "🍫 Мокачино": 230,
    "🤍 Раф": 400,
    "🧊 Раф со льдом": 370
}

MAIN_MENU = ReplyKeyboardMarkup(
    resize_keyboard=True,
    keyboard=[
        [KeyboardButton("☕ Капучино — 250₽")],
        [KeyboardButton("🥛 Латте — 270₽"), KeyboardButton("🍵 Чай — 180₽")],
        [KeyboardButton("⚡ Эспрессо — 200₽"), KeyboardButton("☕ Американо — 300₽")],
        [KeyboardButton("🍫 Мокачино — 230₽"), KeyboardButton("🤍 Раф — 400₽")],
        [KeyboardButton("🧊 Раф со льдом — 370₽")],
        [KeyboardButton("📋 Бронь столика"), KeyboardButton("❓ Помощь")],
        [KeyboardButton("🔧 Настроить уведомления")]
    ]
)

# 🧠 STATES
class OrderStates(StatesGroup):
    waiting_quantity = State()
    waiting_confirm = State()

# 🔔 START
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    await message.reply(
        "☕ *Добро пожаловать в Кофейню «Уют»* ☕\n\n"
        "Выберите товар из меню ниже:",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )

# 🔧 ДЕМО КНОПКА (ЛИДЫ)
@dp.message_handler(lambda m: m.text == "🔧 Настроить уведомления")
async def demo_click(message: types.Message):
    logger.info(f"🎉 ДЕМО! user={message.from_user.id}")
    
    await bot.send_message(
        ADMIN_ID,
        f"🎉 **НОВЫЙ КЛИЕНТ ХОЧЕТ ДЕМО!**\n\n"
        f"🆔 `{message.from_user.id}`\n"
        f"👤 @{message.from_user.username or 'no_username'}\n"
        f"📱 {message.from_user.first_name or 'no_name'}\n"
        f"⏰ {__import__('datetime').datetime.now().strftime('%d.%m %H:%M')}",
        parse_mode="Markdown"
    )
    
    await message.reply(
        "✅ *Уведомления настроены!* 🎉\n\n"
        "🔥 Теперь все заказы будут приходить админу!\n"
        "Тестируйте меню ☕",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )

# 🛒 ЗАКАЗЫ
@dp.message_handler(lambda message: any(item in message.text for item in CAFE_MENU.keys()))
async def process_order(message: types.Message, state: FSMContext):
    logger.info(f"☕ ORDER: '{message.text}' от {message.from_user.id}")
    
    for item_name, price in CAFE_MENU.items():
        if item_name in message.text:
            await state.update_data(item=item_name, price=price)
            await message.reply(
                f"*{item_name}* — {price}₽\n\n"
                "Отличный выбор 😊\n\n"
                f"*Сколько порций?*",
                reply_markup=ReplyKeyboardMarkup(
                    resize_keyboard=True, one_time_keyboard=True,
                    keyboard=[["1", "2", "3+"], ["❌ Отмена"]]
                ),
                parse_mode="Markdown"
            )
            await OrderStates.waiting_quantity.set()
            return
    
    await message.reply("❌ Товар не найден. Выберите из меню.", reply_markup=MAIN_MENU)

# 🔢 КОЛИЧЕСТВО
@dp.message_handler(state=OrderStates.waiting_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        return await message.reply("❌ Заказ отменён.", reply_markup=MAIN_MENU)
    
    try:
        quantity = 3 if message.text == "3+" else int(message.text)
        data = await state.get_data()
        total = data['price'] * quantity
        
        await state.update_data(quantity=quantity, total=total)
        
        await message.reply(
            f"*📋 Ваш заказ:*\n\n"
            f"`{data['item']}` × *{quantity}*\n"
            f"*Итого:* `{total}₽`\n\n"
            f"*Подтвердить заказ?*",
            reply_markup=ReplyKeyboardMarkup(
                resize_keyboard=True, one_time_keyboard=True,
                keyboard=[["✅ Подтвердить", "❌ Отмена"]]
            ),
            parse_mode="Markdown"
        )
        await OrderStates.waiting_confirm.set()
    except:
        await message.reply("❌ Введите число: 1, 2, 3+ или Отмена")

# ✅ ПОДТВЕРЖДЕНИЕ
@dp.message_handler(state=OrderStates.waiting_confirm)
async def process_confirm(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    if "Подтвердить" in message.text:
        # 📤 АДМИНУ
        admin_msg = (
            f"☕ *НОВЫЙ ЗАКАЗ* ☕\n\n"
            f"*{data['item']}* × {data['quantity']}\n"
            f"💰 *{data['total']}₽*\n\n"
            f"👤 @{message.from_user.username or 'no_username'}\n"
            f"🆔 `{message.from_user.id}`\n"
            f"📞 {CAFE_PHONE}"
        )
        await bot.send_message(ADMIN_ID, admin_msg, parse_mode="Markdown")
        logger.info("✅ АДМИН ПОЛУЧИЛ ЗАКАЗ!")
        
        # 👤 КЛИЕНТУ
        await message.reply(
            f"🎉 *Заказ принят!*\n\n"
            "Спасибо! Уже готовим ☕\n\n"
            f"📞 *{CAFE_PHONE}*",
            reply_markup=MAIN_MENU,
            parse_mode="Markdown"
        )
        logger.info("✅ ЗАКАЗ УСПЕШЕН!")
    else:
        await message.reply("❌ Заказ отменён.", reply_markup=MAIN_MENU)
    
    await state.finish()

# ❓ ПОМОЩЬ
@dp.message_handler(lambda m: m.text == "❓ Помощь")
async def help_cmd(message: types.Message):
    await message.reply(
        f"☕ *Помощь по заказу:*\n\n"
        "1️⃣ Выберите товар из меню\n"
        "2️⃣ Укажите количество порций\n"
        "3️⃣ Подтвердите заказ\n\n"
        f"📞 {CAFE_PHONE}\n"
        "⏰ 8:00-23:00 ежедневно",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )

# 📋 БРОНЬ
@dp.message_handler(lambda m: m.text == "📋 Бронь столика")
async def booking(message: types.Message):
    await message.reply(
        f"📋 *Бронь столика*\n\n"
        f"📞 Позвоните: *{CAFE_PHONE}*\n"
        "⏰ Режим: 8:00-23:00\n"
        "🪑 Свободно 24/7",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )

# 🛑 ОТМЕНА В ЛЮБОМ СОСТОЯНИИ
@dp.message_handler(lambda m: m.text == "❌ Отмена", state="*")
async def cancel_any(message: types.Message, state: FSMContext):
    await state.finish()
    await message.reply("❌ Заказ отменён. Выберите из меню ☕", reply_markup=MAIN_MENU)

# 🛠️ DEFAULT ОБРАБОТЧИК
@dp.message_handler(state="*")
async def unknown_cmd(message: types.Message, state: FSMContext):
    await message.reply("👆 Выберите товар из меню ниже!", reply_markup=MAIN_MENU)

# 🛑 ОШИБКИ
@dp.errors_handler()
async def errors_handler(update, exception):
    logger.error(f"❌ ОШИБКА: {exception}")
    if isinstance(exception, CantParseEntities):
        logger.info("⚠️ Markdown ошибка игнорируется")
    return True

# 🚀 WEBHOOK (Render)
async def on_startup(_):
    webhook_url = "https://cafebotify.onrender.com/webhook"  # ← ТВОЙ URL!
    await bot.set_webhook(webhook_url)
    logger.info("✅ WEBHOOK УСТАНОВЛЕН!")

async def on_shutdown(_):
    await bot.delete_webhook()
    logger.info("🔴 BOT STOPPED")

if __name__ == '__main__':
    PORT = int(os.getenv("PORT", 8080))
    logger.info(f"🚀 WEBHOOK START | PORT: {PORT}")
    
    executor.start_webhook(
        dispatcher=dp,
        webhook_path='/webhook',
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        host='0.0.0.0',
        port=PORT
    )
