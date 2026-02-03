import logging
import os
import re
import random
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage

# ================== CONFIG.JSON ==================
def load_config():
    """Загружает config.json для каждого кафе"""
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)["cafe"]
    except:
        logging.error("❌ config.json не найден!")
        return {}

CAFE = load_config()

# ================== КОМПЛИМЕНТЫ ==================
ORDER_COMPLIMENTS = [
    "Отличный выбор 😊",
    "Часто берут, очень уютный напиток ☕",
    "Хороший вариант для хорошего дня 🌞", 
    "Любимый напиток наших гостей ❤️",
]

ORDER_THANKS = [
    "Спасибо за заказ! Уже готовим ☕",
    "Мы получили заказ, будем рады вас видеть 😊",
    "Заказ принят, скоро всё будет готово! ✨",
]

BOOKING_THANKS = [
    "Спасибо! Мы получили вашу заявку 😊",
    "Отличный выбор времени, будем рады вас видеть ☕",
    "Заявка принята, скоро с вами свяжутся! 📞",
]

# ================== ИНИЦИАЛИЗАЦИЯ ==================
logging.basicConfig(level=logging.INFO)
load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ================== ДИНАМИЧЕСКОЕ МЕНЮ ==================
def get_main_menu():
    """Меню для текущего кафе из config.json"""
    menu = ReplyKeyboardMarkup(resize_keyboard=True)
    for item, price in CAFE["menu"].items():
        menu.add(KeyboardButton(f"{item} — {price}₽"))
    menu.add(KeyboardButton("📋 Бронь столика"))
    menu.add(KeyboardButton("❓ Помощь"))
    menu.add(KeyboardButton("🔧 Настроить уведомления"))
    return menu

MAIN_MENU = get_main_menu()

# ================== FSM СОСТОЯНИЯ ==================
class OrderForm(StatesGroup):
    waiting_quantity = State()
    waiting_confirm = State()

class BookingForm(StatesGroup):
    waiting_datetime = State()
    waiting_people = State()

# ================== /START ==================
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    await message.reply(
        f"👋 Добро пожаловать в **{CAFE['name']}**!\n\n"
        f"🔧 *Сначала настройте уведомления!*\n"
        "☕ Выберите напиток или действие ниже 😊",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )

# ================== 🔧 НАСТРОИТЬ УВЕДОМЛЕНИЯ ==================
@dp.message_handler(lambda m: m.text == "🔧 Настроить уведомления")
async def setup_notifications(message: types.Message):
    await bot.send_message(
        CAFE["admin_chat_id"],
        f"✅ *Новый клиент настроил уведомления!*\n\n"
        f"🆔 *Chat ID:* `{message.from_user.id}`\n"
        f"👤 *@{message.from_user.username or 'no_username'}*\n"
        f"📱 *{message.from_user.first_name or 'Имя скрыто'}*\n\n"
        f"🔥 *Готов создать бота!*\n"
        f"1. Fork → config.json → `{message.from_user.id}`\n"
        f"2. Render Free → @CafeNameBot\n"
        f"3. 990₽/мес 🚀",
        parse_mode="Markdown"
    )
    
    await message.reply(
        "✅ *Уведомления настроены!*\n\n"
        "🎉 Теперь все заказы будут приходить **ВАМ** в личку 24/7 ☕\n\n"
        "Можете тестировать меню! 😊",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )

# ================== ЗАКАЗ НАПИТКА ==================
@dp.message_handler(lambda m: "—" in m.text and any(item.split(" — ")[0] in m.text for item in CAFE["menu"]))
async def start_order(message: types.Message, state: FSMContext):
    parts = message.text.split(" — ")
    if len(parts) < 2:
        await message.reply("Выберите блюдо из меню ☝️", reply_markup=MAIN_MENU)
        return
        
    item_name = parts[0].strip()
    if item_name not in CAFE["menu"]:
        await message.reply("Выберите блюдо из меню ☝️", reply_markup=MAIN_MENU)
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
async def process_quantity(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await message.reply("❌ Заказ отменён ☕", reply_markup=MAIN_MENU)
        await state.finish()
        return

    qty_map = {"1": 1, "2": 2, "3+": 3}
    if message.text not in qty_map:
        await message.reply("❌ Выберите: `1`, `2`, `3+` или **❌ Отмена**", parse_mode="Markdown")
        return

    qty = qty_map[message.text]
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
        parse_mode="Markdown"
    )
    await OrderForm.waiting_confirm.set()

@dp.message_handler(state=OrderForm.waiting_confirm)
async def confirm_order(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await message.reply("❌ Заказ отменён ☕", reply_markup=MAIN_MENU)
        await state.finish()
        return

    data = await state.get_data()
    
    await bot.send_message(
        CAFE["admin_chat_id"],
        f"☕ **НОВЫЙ ЗАКАЗ** `{CAFE['name']}`\n\n"
        f"**{data['item']}** × {data['quantity']}\n"
        f"💰 **{data['total']}₽**\n\n"
        f"👤 @{message.from_user.username or message.from_user.id}\n"
        f"📞 {CAFE['phone']}",
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

# ================== БРОНЬ СТОЛИКА ==================
@dp.message_handler(lambda m: m.text == "📋 Бронь столика")
async def book_start(message: types.Message, state: FSMContext):
    start_h, end_h = CAFE["work_hours"]
    await message.reply(
        f"**📅 БРОНЬ СТОЛИКА** `{CAFE['name']}`\n\n"
        f"`ДД.ММ ЧЧ:ММ`\n"
        f"**Пример:** `15.02 19:00`\n\n"
        f"🕐 Работаем: **{start_h}:00–{end_h}:00**",
        parse_mode="Markdown"
    )
    await BookingForm.waiting_datetime.set()

@dp.message_handler(state=BookingForm.waiting_datetime)
async def parse_datetime(message: types.Message, state: FSMContext):
    match = re.match(r"^(\d{1,2})\.(\d{1,2})\s+(\d{2}):(\d{2})$", message.text.strip())
    if not match:
        await message.reply("❌ **Неверный формат!**\n\n`15.02 19:00`", parse_mode="Markdown")
        return

    day, month, hour, minute = map(int, match.groups())
    now = datetime.now()
    start_h, end_h = CAFE["work_hours"]

    try:
        booking_dt = now.replace(day=day, month=month, hour=hour, minute=minute)
        if booking_dt <= now:
            booking_dt += timedelta(days=1)

        if hour < start_h or hour >= end_h:
            await message.reply(f"❌ Мы работаем **{start_h}:00–{end_h}:00**", parse_mode="Markdown")
            return

        await state.update_data(dt=booking_dt)

        kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.row("1-2", "3-4")
        kb.row("5+", "❌ Отмена")

        await message.reply(
            f"✅ **{booking_dt.strftime('%d.%m %H:%M')}**\n\n**👥 Сколько человек?**",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await BookingForm.waiting_people.set()

    except:
        await message.reply("❌ Формат: `15.02 19:00`", parse_mode="Markdown")

@dp.message_handler(state=BookingForm.waiting_people)
async def finish_booking(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await message.reply("❌ Заявка отменена ☕", reply_markup=MAIN_MENU)
        await state.finish()
        return

    people_map = {"1-2": 2, "3-4": 4, "5+": 6}
    if message.text not in people_map:
        await message.reply("❌ Выберите: **1-2**, **3-4**, **5+**", parse_mode="Markdown")
        return

    people = people_map[message.text]
    data = await state.get_data()

    await bot.send_message(
        CAFE["admin_chat_id"],
        f"📋 **НОВАЯ ЗАЯВКА НА БРОНЬ** `{CAFE['name']}`\n\n"
        f"🕐 **{data['dt'].strftime('%d.%m %H:%M')}**\n"
        f"👥 **{people} человек**\n"
        f"👤 @{message.from_user.username or message.from_user.id}\n\n"
        f"📞 **{CAFE['phone']}** — перезвонить!",
        parse_mode="Markdown"
    )

    await message.reply(
        f"✅ **Заявка на бронь принята!**\n\n"
        f"{random.choice(BOOKING_THANKS)}\n\n"
        f"📞 **{CAFE['phone']}**",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )
    await state.finish()

# ================== ПОМОЩЬ ==================
@dp.message_handler(lambda m: m.text == "❓ Помощь")
async def help_handler(message: types.Message):
    start_h, end_h = CAFE["work_hours"]
    await message.reply(
        f"**{CAFE['name']} — справка** 😊\n\n"
        f"☕ **Меню** — выберите блюдо → количество → подтвердите\n"
        f"📋 **Бронь** — дата/время → количество человек\n"
        f"🔧 **Уведомления** — все заказы в вашу личку\n\n"
        f"📞 **{CAFE['phone']}** — вопросы\n"
        f"🕐 **{start_h}:00–{end_h}:00**",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )

# ================== FALLBACK ==================
@dp.message_handler()
async def fallback(message: types.Message):
    await message.reply(
        f"👋 **{CAFE['name']}**\n\n"
        "Выберите действие в меню ниже 😊",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )

# ================== WEBHOOK (Render) ==================
if TOKEN:
    WEBHOOK_PATH = f"/webhook/{TOKEN}"
    WEBHOOK_URL = f"https://chatbotify-2tjd.onrender.com{WEBHOOK_PATH}"

    async def on_startup(dp):
        await bot.set_webhook(WEBHOOK_URL)
        print(f"✅ {CAFE.get('name', 'CafeBot')} LIVE на Render!")

    if __name__ == "__main__":
        executor.start_webhook(
            dp,
            WEBHOOK_PATH,
            on_startup=on_startup,
            host="0.0.0.0",
            port=int(os.getenv("PORT", 10000))
        )
else:
    print("❌ TELEGRAM_TOKEN не найден!")
