import logging
import os
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage

# ✅ Встроенный config (НЕ нужен config.py)
CAFE = {
    "name": "CafeBotify Demo ☕",
    "phone": "8 (861) 123-45-67",
    "admin_chat_id": 1471275603,
    "work_hours": [18, 22],
    "menu": {
        "☕ Кофе": 200,
        "🍵 Чай": 150,
        "🥧 Пирог": 100
    }
}

logging.basicConfig(level=logging.INFO)
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ГЛАВНОЕ МЕНЮ
MAIN_MENU = ReplyKeyboardMarkup(resize_keyboard=True)
for item, price in CAFE["menu"].items():
    MAIN_MENU.add(KeyboardButton(f"{item} {price}₽"))
MAIN_MENU.add(KeyboardButton("📋 Бронь столика"))
MAIN_MENU.add(KeyboardButton("❓ Помощь"))

# FSM СОСТОЯНИЯ
class OrderForm(StatesGroup):
    waiting_quantity = State()
    waiting_confirm = State()

class BookingForm(StatesGroup):
    waiting_datetime = State()
    waiting_people = State()

# /START
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    await message.reply(
        f"👋 Добро пожаловать в **{CAFE['name']}** ☕\n\nВыберите действие:",
        reply_markup=MAIN_MENU,
        parse_mode='Markdown'
    )

# ЗАКАЗЫ
@dp.message_handler(lambda m: any(m.text.startswith(name) for name in CAFE["menu"]))
async def start_order(message: types.Message, state: FSMContext):
    parts = message.text.rsplit(" ", 1)
    if len(parts) < 2:
        await message.reply("Выберите блюдо из меню ☝️", reply_markup=MAIN_MENU)
        return
    
    item_name = parts[0]
    if item_name not in CAFE["menu"]:
        await message.reply("Выберите блюдо из меню ☝️", reply_markup=MAIN_MENU)
        return

    price = CAFE["menu"][item_name]
    await state.update_data(item=item_name, price=price)

    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("1", "2", "3+")
    kb.row("❌ Отмена")

    await message.reply(
        f"**{item_name}** — {price}₽\n\n**Сколько порций?**",
        reply_markup=kb,
        parse_mode='Markdown'
    )
    await OrderForm.waiting_quantity.set()

@dp.message_handler(state=OrderForm.waiting_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await message.reply("❌ Заказ отменён ☕", reply_markup=MAIN_MENU)
        await state.finish()
        return

    if message.text not in {"1", "2", "3+"}:
        await message.reply("❌ Выберите: `1`, `2`, `3+` или **❌ Отмена**", parse_mode='Markdown')
        return

    qty = {"1": 1, "2": 2, "3+": 3}[message.text]
    data = await state.get_data()
    total = data["price"] * qty

    await state.update_data(quantity=qty, total=total)

    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("✅ Подтвердить", "❌ Отмена")

    await message.reply(
        f"**📋 Ваш заказ:**\n\n"
        f"`{data['item']}` × **{qty}**\n"
        f"**Итого:** `{total}₽`\n\n"
        "**Подтвердить?**",
        reply_markup=kb,
        parse_mode='Markdown'
    )
    await OrderForm.waiting_confirm.set()

@dp.message_handler(state=OrderForm.waiting_confirm)
async def confirm_order(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await message.reply("❌ Заказ отменён ☕", reply_markup=MAIN_MENU)
        await state.finish()
        return

    data = await state.get_data()
    
    # УВЕДОМЛЕНИЕ АДМИНУ ☕
    await bot.send_message(
        CAFE["admin_chat_id"],
        f"☕ **НОВЫЙ ЗАКАЗ** `{CAFE['name']}`\n\n"
        f"**{data['item']}** × {data['quantity']}\n"
        f"💰 **{data['total']}₽**\n\n"
        f"👤 @{message.from_user.username or message.from_user.id}",
        parse_mode='Markdown'
    )

    await message.reply(
        "🎉 **Заказ принят!**\n\n⏰ Готовим ☕\n📞 {CAFE['phone']}",
        reply_markup=MAIN_MENU,
        parse_mode='Markdown'
    )
    await state.finish()

# БРОНЬ СТОЛИКА 📋
@dp.message_handler(lambda m: m.text == "📋 Бронь столика")
async def book_start(message: types.Message, state: FSMContext):
    start_h, end_h = CAFE["work_hours"]
    await message.reply(
        f"**📅 БРОНЬ СТОЛИКА** `{CAFE['name']}`\n\n"
        f"`ДД.ММ ЧЧ:ММ`\n"
        f"**Пример:** `15.02 19:00`\n\n"
        f"🕐 Работаем: **{start_h}:00–{end_h}:00**",
        parse_mode='Markdown'
    )
    await BookingForm.waiting_datetime.set()

@dp.message_handler(state=BookingForm.waiting_datetime)
async def parse_datetime(message: types.Message, state: FSMContext):
    match = re.match(r"^(\d{1,2})\.(\d{1,2})\s+(\d{2}):(\d{2})$", message.text.strip())
    if not match:
        await message.reply("❌ **Неверный формат!**\n\n`15.02 19:00`", parse_mode='Markdown')
        return

    day, month, hour, minute = map(int, match.groups())
    now = datetime.now()
    start_h, end_h = CAFE["work_hours"]

    try:
        booking_dt = now.replace(day=day, month=month, hour=hour, minute=minute)
        if booking_dt <= now:
            booking_dt += timedelta(days=1)

        if hour < start_h or hour > end_h:
            await message.reply(f"❌ Мы работаем **{start_h}:00–{end_h}:00**", parse_mode='Markdown')
            return

        await state.update_data(dt=booking_dt)

        kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.row("1-2", "3-4")
        kb.row("5+", "❌ Отмена")

        await message.reply(
            f"✅ **{booking_dt.strftime('%d.%m %H:%M')}**\n\n**👥 Сколько человек?**",
            reply_markup=kb,
            parse_mode='Markdown'
        )
        await BookingForm.waiting_people.set()

    except:
        await message.reply("❌ Формат: `15.02 19:00`", parse_mode='Markdown')

@dp.message_handler(state=BookingForm.waiting_people)
async def finish_booking(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await message.reply("❌ Заявка отменена ☕", reply_markup=MAIN_MENU)
        await state.finish()
        return

    if message.text not in {"1-2", "3-4", "5+"}:
        await message.reply("❌ Выберите: **1-2**, **3-4**, **5+**", parse_mode='Markdown')
        return

    people_map = {"1-2": 2, "3-4": 4, "5+": 6}
    people = people_map[message.text]
    data = await state.get_data()

    # ✅ АДМИН ПОЛУЧАЕТ ЗАЯВКУ
    await bot.send_message(
        CAFE["admin_chat_id"],
        f"📋 **НОВАЯ ЗАЯВКА НА БРОНЬ** `{CAFE['name']}`\n\n"
        f"🕐 **{data['dt'].strftime('%d.%m %H:%M')}**\n"
        f"👥 **{people} человек**\n"
        f"👤 @{message.from_user.username or message.from_user.id}\n\n"
        f"📞 **{CAFE['phone']}** — перезвонить!",
        parse_mode='Markdown'
    )

    # ✅ КЛИЕНТ ПОЛУЧАЕТ (БЕЗ ответственности)
    await message.reply(
        f"✅ **Заявка на бронь принята!**\n\n"
        f"Мы свяжемся с Вами для подтверждения в течение 15 минут.\n\n"
        f"📞 **{CAFE['phone']}**",
        reply_markup=MAIN_MENU,
        parse_mode='Markdown'
    )
    await state.finish()

# ПОМОЩЬ
@dp.message_handler(lambda m: m.text == "❓ Помощь")
async def help_handler(message: types.Message):
    start_h, end_h = CAFE["work_hours"]
    await message.reply(
        f"**{CAFE['name']} — справка**\n\n"
        f"☕ **Меню** — выберите блюдо → количество → подтвердите\n"
        f"📋 **Бронь** — дата/время → количество человек\n\n"
        f"📞 **{CAFE['phone']}** — вопросы\n"
        f"🕐 **{start_h}:00–{end_h}:00**",
        reply_markup=MAIN_MENU,
        parse_mode='Markdown'
    )

# FALLBACK
@dp.message_handler()
async def fallback(message: types.Message):
    await message.reply(
        f"👋 **{CAFE['name']}**\n\nВыберите действие в меню ☝️",
        reply_markup=MAIN_MENU,
        parse_mode='Markdown'
    )

# WEBHOOK для Render
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://chatbotify-2tjd.onrender.com{WEBHOOK_PATH}"

async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL)
    print(f"✅ {CAFE['name']} LIVE на Render!")

if __name__ == "__main__":
    executor.start_webhook(
        dp,
        WEBHOOK_PATH,
        on_startup=on_startup,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 10000))
    )

