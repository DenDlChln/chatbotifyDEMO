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

# ================== КОНФИГУРАЦИЯ ==================
def load_config():
    """Загружает настройки кафе из config.json"""
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)["cafe"]
            config["admin_chat_id"] = int(config["admin_chat_id"])
            print(f"🔍 CONFIG: Загружен {config.get('name')}")
            print(f"🔍 CONFIG: admin_chat_id = {config['admin_chat_id']}")
            return config
    except Exception as e:
        print(f"💥 CONFIG ОШИБКА: {e}")
        return {}

CAFE = load_config()

# ================== ТЕКСТЫ ==================
ORDER_COMPLIMENTS = [
    "Отличный выбор 😊", "Хороший вкус ☕", "Популярный напиток ❤️", 
    "Ваш любимый вариант ✨"
]

ORDER_THANKS = [
    "Спасибо! Уже готовим ☕", "Заказ принят 😊", "Ждём вас! ✨"
]

BOOKING_THANKS = [
    "Заявка принята! 📞", "Скоро перезвоним 😊", "Бронь подтверждена ✅"
]

# ================== ИНИЦИАЛИЗАЦИЯ ==================
logging.basicConfig(level=logging.INFO)
load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
print(f"🔍 TOKEN: {len(TOKEN) if TOKEN else 'ПУСТОЙ'} символов")
if not TOKEN or ' ' in TOKEN:
    print("💥 TELEGRAM_TOKEN не найден!")
    exit(1)

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

def get_main_menu():
    menu = ReplyKeyboardMarkup(resize_keyboard=True)
    for item, price in CAFE.get("menu", {}).items():
        menu.add(KeyboardButton(f"{item} — {price}₽"))
    menu.add(KeyboardButton("📋 Бронь столика"))
    menu.add(KeyboardButton("❓ Помощь"))
    return menu

MAIN_MENU = get_main_menu()

# ================== FSM СОСТОЯНИЯ ==================
class OrderForm(StatesGroup):
    waiting_quantity = State()
    waiting_confirm = State()

class BookingForm(StatesGroup):
    waiting_datetime = State()
    waiting_people = State()

# ================== /START с DEBUG ==================
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    print(f"🔥 START: user_id={message.from_user.id}")
    print(f"🔥 START: username=@{message.from_user.username}")
    
    admin_id = CAFE.get("admin_chat_id")
    print(f"🔥 START: admin_id={admin_id}")
    
    # 🔥 ТЕСТ УВЕДОМЛЕНИЯ ПРЯМО СЕЙЧАС!
    if admin_id:
        try:
            await bot.send_message(
                admin_id, 
                f"🧪 ТЕСТ /start!\n"
                f"Клиент: {message.from_user.id}\n"
                f"@{message.from_user.username or 'no_username'}\n"
                f"Время: {datetime.now().strftime('%H:%M:%S')}"
            )
            print("✅ ТЕСТ /start АДМИНУ ДОШЁЛ!")
        except Exception as e:
            print(f"💥 ОШИБКА ТЕСТ /start: {e}")
    
    await message.reply(
        f"👋 Добро пожаловать в **{CAFE.get('name', 'Кофейню')}** ☕\n\n"
        f"Выберите товар из меню ниже:",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )

# ================== ЗАКАЗЫ ☕ с ГРОМКИМ DEBUG ==================
@dp.message_handler(lambda m: any(f"{item} — {price}₽" == m.text.strip() for item, price in CAFE.get("menu", {}).items()))
async def start_order(message: types.Message, state: FSMContext):
    print(f"🔥 ORDER START: {message.text} от {message.from_user.id}")
    
    for item_name, price in CAFE.get("menu", {}).items():
        if f"{item_name} — {price}₽" == message.text.strip():
            await state.finish()
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
            return

@dp.message_handler(state=OrderForm.waiting_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    print(f"🔥 QUANTITY: {message.text} от {message.from_user.id}")
    
    if message.text == "❌ Отмена":
        await state.finish()
        await message.reply("❌ Заказ отменён", reply_markup=MAIN_MENU)
        return

    qty_map = {"1": 1, "2": 2, "3+": 3}
    if message.text not in qty_map:
        await message.reply("❌ Выберите: **1**, **2**, **3+** или **❌ Отмена**", parse_mode="Markdown")
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
    print("🔥🔥🔥 CONFIRM_ORDER НАЧАЛСЯ!")
    print(f"🔥🔥🔥 USER: {message.from_user.id}")
    
    data = await state.get_data()
    admin_id = CAFE.get("admin_chat_id")
    
    print(f"🔥🔥🔥 DATA: {data}")
    print(f"🔥🔥🔥 ADMIN_ID: {admin_id}")
    print(f"🔥🔥🔥 CAFE: {CAFE}")
    
    if not admin_id:
        print("💥💥💥 АДМИН ID ОТСУТСТВУЕТ!")
        await message.reply("❌ Ошибка config.json!")
        await state.finish()
        return

    # 🔥 ГРОМЧЕЙШЕЕ ТЕСТ-СООБЩЕНИЕ
    test_msg = f"""
🧪🔥 DEBUG ЗАКАЗ #{random.randint(1000,9999)}
━━━━━━━━━━━━━━━━━━━━━
Admin ID: `{admin_id}`
Товар: **{data.get('item', 'НЕИЗВЕСТНО')}**
Кол-во: {data.get('quantity', 'НЕИЗВЕСТНО')}
Сумма: {data.get('total', 'НЕИЗВЕСТНО')}₽
Клиент: `{message.from_user.id}`
Username: @{message.from_user.username or 'нет'}
Время: {datetime.now().strftime('%d.%m %H:%M:%S')}
━━━━━━━━━━━━━━━━━━━━━
"""

    print("🔥🔥🔥 ОТПРАВЛЯЕМ АДМИНУ...")
    try:
        await bot.send_message(admin_id, test_msg, parse_mode="Markdown")
        print("✅✅✅ АДМИН ПОЛУЧИЛ ЗАКАЗ!")
    except Exception as e:
        print(f"💥💥💥 ОШИБКА АДМИНА: {e}")
        await message.reply(f"⚠️ Ошибка админа: {str(e)[:100]}")
    
    print("🔥🔥🔥 Клиенту: Заказ принят!")
    await message.reply(
        f"🎉 **Заказ принят!**\n\n"
        f"{random.choice(ORDER_THANKS)}\n\n"
        f"📞 **{CAFE.get('phone', '+7 (XXX) XXX-XX-XX')}**",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )
    await state.finish()
    print("🔥🔥🔥 CONFIRM_ORDER ЗАВЁРШЁН!")

# ================== БРОНЬ СТОЛИКА ==================
@dp.message_handler(lambda m: m.text == "📋 Бронь столика")
async def book_start(message: types.Message, state: FSMContext):
    print(f"🔥 BOOKING START: {message.from_user.id}")
    await state.finish()
    work_hours = CAFE.get("work_hours", [9, 22])
    start_h, end_h = work_hours
    
    await message.reply(
        f"**📅 БРОНЬ СТОЛИКА** `{CAFE.get('name')}`\n\n"
        f"`ДД.ММ ЧЧ:ММ`\n"
        f"**Пример:** `15.02 19:00`\n\n"
        f"🕐 Работаем: **{start_h}:00–{end_h}:00**",
        parse_mode="Markdown"
    )
    await BookingForm.waiting_datetime.set()

@dp.message_handler(state=BookingForm.waiting_datetime)
async def parse_datetime(message: types.Message, state: FSMContext):
    print(f"🔥 BOOKING DATE: {message.text}")
    match = re.match(r"^(\d{1,2})\.(\d{1,2})\s+(\d{2}):(\d{2})$", message.text.strip())
    if not match:
        await message.reply("❌ **Неверный формат!**\n\n`15.02 19:00`", parse_mode="Markdown")
        return

    day, month, hour, minute = map(int, match.groups())
    now = datetime.now()
    work_hours = CAFE.get("work_hours", [9, 22])
    start_h, end_h = work_hours

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

    except Exception:
        await message.reply("❌ Формат: `15.02 19:00`", parse_mode="Markdown")

@dp.message_handler(state=BookingForm.waiting_people)
async def finish_booking(message: types.Message, state: FSMContext):
    print(f"🔥 BOOKING FINISH: {message.text}")
    
    if message.text == "❌ Отмена":
        await state.finish()
        await message.reply("❌ Заявка отменена", reply_markup=MAIN_MENU)
        return

    people_map = {"1-2": 2, "3-4": 4, "5+": 6}
    if message.text not in people_map:
        await message.reply("❌ Выберите: **1-2**, **3-4**, **5+**", parse_mode="Markdown")
        return

    people = people_map[message.text]
    data = await state.get_data()
    admin_id = CAFE.get("admin_chat_id")

    try:
        await bot.send_message(
            admin_id,
            f"📋 **НОВАЯ БРОНЬ** `{CAFE.get('name')}`\n\n"
            f"🕐 **{data['dt'].strftime('%d.%m %H:%M')}**\n"
            f"👥 **{people} человек**\n"
            f"👤 @{message.from_user.username or str(message.from_user.id)}\n"
            f"🆔 `{message.from_user.id}`",
            parse_mode="Markdown"
        )
        print("✅ БРОНЬ АДМИНУ ДОШЛА!")
    except Exception as e:
        print(f"💥 ОШИБКА БРОНИ: {e}")

    await message.reply(
        f"✅ **Бронь принята!**\n\n"
        f"🕐 **{data['dt'].strftime('%d.%m %H:%M')}**\n"
        f"👥 **{people} человек**",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )
    await state.finish()

# ================== ПОМОЩЬ ==================
@dp.message_handler(lambda m: m.text == "❓ Помощь")
async def help_handler(message: types.Message):
    print(f"🔥 HELP: {message.from_user.id}")
    work_hours = CAFE.get("work_hours", [9, 22])
    start_h, end_h = work_hours
    await message.reply(
        f"**{CAFE.get('name')}** — справка ☕\n\n"
        f"☕ **Меню** — выберите товар → количество → подтвердите\n"
        f"📋 **Бронь** — дата/время → количество человек\n\n"
        f"📞 **{CAFE.get('phone', '+7 (XXX) XXX-XX-XX')}**\n"
        f"🕐 **{start_h}:00–{end_h}:00**",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )

# ================== FALLBACK ==================
@dp.message_handler()
async def fallback(message: types.Message, state: FSMContext):
    print(f"🔥 FALLBACK: {message.text} от {message.from_user.id}")
    await state.finish()
    await message.reply(
        f"👋 **{CAFE.get('name')}**\n\n"
        "Выберите из меню ☕",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )

# ================== ОШИБКИ ==================
@dp.errors_handler()
async def errors_handler(update, exception):
    print(f"💥 ГЛОБАЛЬНАЯ ОШИБКА: {exception}")
    return True

# ================== WEBHOOK ==================
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://chatbotify-2tjd.onrender.com{WEBHOOK_PATH}"

async def on_startup(dp):
    print("🚀 BOT STARTUP!")
    print(f"🚀 WEBHOOK: {WEBHOOK_URL}")
    print(f"🚀 CAFE: {CAFE.get('name')}")
    print(f"🚀 ADMIN: {CAFE.get('admin_chat_id')}")
    
    await bot.set_webhook(WEBHOOK_URL)
    print("✅ WEBHOOK УСТАНОВЛЕН!")

if __name__ == "__main__":
    print("🎬 ЗАПУСК БОТА...")
    executor.start_webhook(
        dp, WEBHOOK_PATH, on_startup=on_startup,
        host="0.0.0.0", port=int(os.getenv("PORT", 10000))
    )
