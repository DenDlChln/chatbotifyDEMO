import logging
import os
import re
import random
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage

# ================== КОНФИГ ==================

CAFE = {
    "name": "Кофейня «Уют» ☕",
    "phone": "+7 991 079-58-37",
    "admin_chat_id": 1471275603,
    "work_hours": (9, 21),
    "menu": {
        "☕ Капучино": 250,
        "🥛 Латте": 270,
        "🍵 Чай": 180,
        "⚡ Эспрессо": 200,
        "☕ Американо": 300,
        "🍫 Мокачино": 230,
        "🤍 Раф": 400,
        "🧊 Раф со льдом": 370
    }
}

ORDER_COMPLIMENTS = [
    "Отличный выбор ☕",
    "Часто берут, очень уютный напиток",
    "Хороший вариант для хорошего дня 🙂",
    "Любимый напиток наших гостей",
]

ORDER_THANKS = [
    "Спасибо за заказ! Уже готовим ☕",
    "Мы получили заказ, будем рады вас видеть 🙂",
    "Заказ принят, скоро всё будет готово",
]

BOOKING_THANKS = [
    "Спасибо! Мы получили вашу заявку 🙂",
    "Отличный выбор времени, будем рады вас видеть",
    "Заявка принята, скоро с вами свяжутся ☕",
]

# ================== ИНИЦИАЛИЗАЦИЯ ==================

logging.basicConfig(level=logging.INFO)
load_dotenv()

bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
dp = Dispatcher(bot, storage=MemoryStorage())

# ================== МЕНЮ ==================

MAIN_MENU = ReplyKeyboardMarkup(resize_keyboard=True)
for name, price in CAFE["menu"].items():
    MAIN_MENU.add(KeyboardButton(f"{name} — {price}₽"))
MAIN_MENU.add(KeyboardButton("📋 Бронь столика"))
MAIN_MENU.add(KeyboardButton("❓ Помощь"))

# ================== FSM ==================

class OrderForm(StatesGroup):
    waiting_quantity = State()
    waiting_confirm = State()

class BookingForm(StatesGroup):
    waiting_datetime = State()
    waiting_people = State()

# ================== START ==================

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    await message.reply(
        f"👋 Добро пожаловать в **{CAFE['name']}**\n\n"
        "Выберите напиток или действие ниже ☕",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )

# ================== ЗАКАЗ ==================

@dp.message_handler(lambda m: "—" in m.text)
async def start_order(message: types.Message, state: FSMContext):
    item_name = message.text.split(" — ")[0]

    if item_name not in CAFE["menu"]:
        return

    price = CAFE["menu"][item_name]
    await state.update_data(item=item_name, price=price)

    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("1", "2", "3+")
    kb.row("❌ Отмена")

    await message.reply(
        f"**{item_name}** — {price}₽\n\n"
        f"{random.choice(ORDER_COMPLIMENTS)}\n\n"
        "**Сколько порций?**",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await OrderForm.waiting_quantity.set()

@dp.message_handler(state=OrderForm.waiting_quantity)
async def quantity(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.reply("Заказ отменён ☕", reply_markup=MAIN_MENU)
        return

    qty = {"1": 1, "2": 2, "3+": 3}.get(message.text)
    if not qty:
        return

    data = await state.get_data()
    total = qty * data["price"]
    await state.update_data(quantity=qty, total=total)

    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("✅ Подтвердить", "❌ Отмена")

    await message.reply(
        f"📋 **Ваш заказ:**\n"
        f"{data['item']} × {qty}\n"
        f"💰 **{total}₽**\n\nПодтвердить?",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await OrderForm.waiting_confirm.set()

@dp.message_handler(state=OrderForm.waiting_confirm)
async def confirm(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.
finish()
        await message.reply("Заказ отменён ☕", reply_markup=MAIN_MENU)
        return

    data = await state.get_data()

    await bot.send_message(
        CAFE["admin_chat_id"],
        f"☕ **НОВЫЙ ЗАКАЗ**\n"
        f"{data['item']} × {data['quantity']}\n"
        f"💰 {data['total']}₽",
        parse_mode="Markdown"
    )

    await message.reply(
        f"🎉 **Заказ принят!**\n\n"
        f"{random.choice(ORDER_THANKS)}\n\n"
        f"📞 {CAFE['phone']}",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )
    await state.finish()

# ================== БРОНЬ ==================

@dp.message_handler(lambda m: m.text == "📋 Бронь столика")
async def booking_start(message: types.Message, state: FSMContext):
    start_h, end_h = CAFE["work_hours"]
    await message.reply(
        f"📅 **Бронь столика**\n\n"
        f"`ДД.ММ ЧЧ:ММ`\n"
        f"Пример: `15.02 19:00`\n\n"
        f"🕐 Работаем: **{start_h}:00–{end_h}:00**",
        parse_mode="Markdown"
    )
    await BookingForm.waiting_datetime.set()

@dp.message_handler(state=BookingForm.waiting_datetime)
async def booking_datetime(message: types.Message, state: FSMContext):
    match = re.match(r"(\d{1,2})\.(\d{1,2}) (\d{2}):(\d{2})", message.text)
    if not match:
        return

    day, month, hour, minute = map(int, match.groups())
    dt = datetime.now().replace(day=day, month=month, hour=hour, minute=minute)
    await state.update_data(dt=dt)

    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("1-2", "3-4", "5+")
    kb.row("❌ Отмена")

    await message.reply(
        f"✅ **{dt.strftime('%d.%m %H:%M')}**\n\nСколько человек?",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await BookingForm.waiting_people.set()

@dp.message_handler(state=BookingForm.waiting_people)
async def booking_finish(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.reply("Бронь отменена ☕", reply_markup=MAIN_MENU)
        return

    data = await state.get_data()

    await bot.send_message(
        CAFE["admin_chat_id"],
        f"📋 **НОВАЯ БРОНЬ**\n"
        f"{data['dt'].strftime('%d.%m %H:%M')}\n"
        f"👥 {message.text}",
        parse_mode="Markdown"
    )

    await message.reply(
        f"✅ **Заявка принята!**\n\n"
        f"{random.choice(BOOKING_THANKS)}\n\n"
        f"📞 {CAFE['phone']}",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )
    await state.finish()

# ================== WEBHOOK ==================

WEBHOOK_PATH = f"/webhook/{os.getenv('TELEGRAM_TOKEN')}"
WEBHOOK_URL = f"https://chatbotify-2tjd.onrender.com{WEBHOOK_PATH}"

async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL)
    print("✅ Уют LIVE")

if name == "__main__":
    executor.start_webhook(
        dp,
        WEBHOOK_PATH,
        on_startup=on_startup,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 10000))
    )
