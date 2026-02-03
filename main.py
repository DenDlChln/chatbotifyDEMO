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
from aiogram.contrib.fsm_storage.memory import MemoryStorage  # ✅ Render Free!

# ================== CONFIG ==================
def load_config():
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)["cafe"]
    except:
        logging.error("❌ config.json не найден!")
        return {}

CAFE = load_config()

ORDER_COMPLIMENTS = [
    "Отличный выбор 😊", "Часто берут, очень уютный напиток ☕",
    "Хороший вариант для хорошего дня 🌞", "Любимый напиток наших гостей ❤️",
]

ORDER_THANKS = [
    "Спасибо за заказ! Уже готовим ☕", "Мы получили заказ, будем рады вас видеть 😊",
    "Заказ принят, скоро всё будет готово! ✨",
]

# ================== INIT ==================
logging.basicConfig(level=logging.INFO)
load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN or ' ' in TOKEN:
    print("❌ TELEGRAM_TOKEN не найден!")
    exit(1)

bot = Bot(token=TOKEN)
storage = MemoryStorage()  # ✅ Render Free!
dp = Dispatcher(bot, storage=storage)

def get_main_menu():
    menu = ReplyKeyboardMarkup(resize_keyboard=True)
    for item, price in CAFE["menu"].items():
        menu.add(KeyboardButton(f"{item} — {price}₽"))
    menu.add(KeyboardButton("📋 Бронь столика"))
    menu.add(KeyboardButton("❓ Помощь"))
    menu.add(KeyboardButton("🔧 Настроить уведомления"))
    return menu

MAIN_MENU = get_main_menu()

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
        f"👋 Добро пожаловать в **{CAFE['name']}**!\n\n"
        "🔧 *Сначала настройте уведомления!*\n"
        "☕ Выберите напиток ниже 😊",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )

# ================== НАСТРОЙКИ ==================
@dp.message_handler(lambda m: m.text == "🔧 Настроить уведомления")
async def setup_notifications(message: types.Message):
    await bot.send_message(
        CAFE["admin_chat_id"],
        f"✅ *Новый клиент!*\n🆔 `{message.from_user.id}`\n👤 @{message.from_user.username}",
        parse_mode="Markdown"
    )
    await message.reply("✅ *Уведомления настроены!* ☕ Тестируйте меню!", 
                       reply_markup=MAIN_MENU, parse_mode="Markdown")

# ================== ЗАКАЗ ☕ ==================
@dp.message_handler(lambda m: any(f"{item} — {price}₽" == m.text.strip() for item, price in CAFE["menu"].items()))
async def start_order(message: types.Message, state: FSMContext):
    for item_name, price in CAFE["menu"].items():
        if f"{item_name} — {price}₽" == message.text.strip():
            await state.update_data(item=item_name, price=price)
            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.row("1", "2", "3+").row("❌ Отмена")
            await message.reply(
                f"**{item_name}** — {price}₽\n\n{random.choice(ORDER_COMPLIMENTS)}\n\n**Сколько порций?**",
                reply_markup=kb, parse_mode="Markdown"
            )
            await OrderForm.waiting_quantity.set()
            return

@dp.message_handler(state=OrderForm.waiting_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await message.reply("❌ Отменено ☕", reply_markup=MAIN_MENU)
        await state.finish()
        return
    qty_map = {"1": 1, "2": 2, "3+": 3}
    if message.text not in qty_map:
        await message.reply("❌ 1, 2 или 3+")
        return
    qty = qty_map[message.text]
    data = await state.get_data()
    total = data["price"] * qty
    await state.update_data(quantity=qty, total=total)
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("✅ Подтвердить", "❌ Отмена")
    await message.reply(
        f"**📋 Заказ:**\n\n`{data['item']}` × {qty}\n**Итого: {total}₽**\n\n**Подтвердить?**",
        reply_markup=kb, parse_mode="Markdown"
    )
    await OrderForm.waiting_confirm.set()

@dp.message_handler(state=OrderForm.waiting_confirm)
async def confirm_order(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await message.reply("❌ Отменено ☕", reply_markup=MAIN_MENU)
        await state.finish()
        return
    data = await state.get_data()
    await bot.send_message(
        CAFE["admin_chat_id"],
        f"☕ **НОВЫЙ ЗАКАЗ {CAFE['name']}**\n\n**{data['item']}** × {data['quantity']}\n💰 **{data['total']}₽**\n👤 @{message.from_user.username}",
        parse_mode="Markdown"
    )
    await message.reply(f"🎉 **Заказ принят!**\n\n{random.choice(ORDER_THANKS)}\n📞 {CAFE['phone']}", 
                       reply_markup=MAIN_MENU, parse_mode="Markdown")
    await state.finish()

# ================== FALLBACK ==================
@dp.message_handler()
async def fallback(message: types.Message):
    await message.reply(f"👋 **{CAFE['name']}**\nВыберите из меню ☕", reply_markup=MAIN_MENU, parse_mode="Markdown")

# ================== WEBHOOK ==================
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://chatbotify-2tjd.onrender.com{WEBHOOK_PATH}"

async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL)
    print(f"✅ {CAFE.get('name', 'CafeBot')} LIVE!")

if __name__ == "__main__":
    executor.start_webhook(dp, WEBHOOK_PATH, on_startup=on_startup, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
