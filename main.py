import logging
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram_calendar import DialogCalendar, DIALOG_CALENDAR

logging.basicConfig(level=logging.INFO)

# Токен из .env
load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

# Главное меню - ДОБАВИЛИ БРОНЬ! 🔥
MAIN_MENU = ReplyKeyboardMarkup(resize_keyboard=True)
MAIN_MENU.row(KeyboardButton('☕ Кофе 200₽'), KeyboardButton('📋 Бронь столика'))  # ← НОВОЕ!
MAIN_MENU.row(KeyboardButton('🍵 Чай 150₽'), KeyboardButton('🛒 Оформить заказ'))
MAIN_MENU.row(KeyboardButton('❓ Помощь'))

# СОСТОЯНИЯ ДЛЯ БРОНИРОВАНИЯ
class BookingForm(StatesGroup):
    date = State()
    time = State()
    people = State()
    name = State()
    phone = State()

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    await message.reply(
        "👋 Привет!\n\n☕️ **МЕНЮ КАФЕ BOTIFY**\n\n"
        "☕ Кофе 200₽\n"
        "🍵 Чай 150₽\n"
        "🥧 Пирог 100₽\n"
        "📋 Бронь столика\n\n"
        "_Выбери кнопку или напиши заказ_",
        reply_markup=MAIN_MENU,
        parse_mode='Markdown'
    )

# 🆕 БРОНИРОВАНИЕ СТОЛИКА
@dp.message_handler(lambda message: message.text == '📋 Бронь столика')
async def book_table_start(message: types.Message):
    await BookingForm.date.set()
    await message.reply("📅 Выберите дату бронирования:", 
                       reply_markup=ReplyKeyboardMarkup(resize_keyboard=True))
    await DialogCalendar().start_calendar(bot, message)

@dp.callback_query_handler(DIALOG_CALENDAR, state=BookingForm.date)
async def pick_date(callback_query: types.CallbackQuery, state: FSMContext):
    await BookingForm.next()
    await state.update_data(date=callback_query.data)
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    times = ["18:00", "19:00", "20:00", "21:00"]
    for t in times:
        keyboard.add(InlineKeyboardButton(t, callback_data=f"time_{t}"))
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_booking"))
    
    await callback_query.message.edit_text(
        f"⏰ Выберите время на {callback_query.data}:",
        reply_markup=keyboard
    )

@dp.callback_query_handler(state=BookingForm.time, lambda c: c.data.startswith('time_'))
async def pick_time(callback_query: types.CallbackQuery, state: FSMContext):
    await state.update_data(time=callback_query.data.replace('time_', ''))
    await callback_query.message.edit_text(
        "👥 Сколько человек?",
        reply_markup=InlineKeyboardMarkup(row_width=1).add(
            InlineKeyboardButton("👤 1-2", callback_data="people_2"),
            InlineKeyboardButton("👥 3-4", callback_data="people_4"),
            InlineKeyboardButton("👨‍👩‍👧‍👦 5+", callback_data="people_6"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel_booking")
        )
    )

@dp.callback_query_handler(state=BookingForm.time, lambda c: c.data.startswith('people_'))
async def pick_people(callback_query: types.CallbackQuery, state: FSMContext):
    await state.update_data(people=callback_query.data.replace('people_', ''))
    data = await state.get_data()
    
    await callback_query.message.edit_text(
        f"✅ **Бронь подтверждена!**\n\n"
        f"📅 {data['date']}\n"
        f"⏰ {data['time']}\n"
        f"👥 {data['people']} человек\n\n"
        f"📞 Позвоните для подтверждения:\n"
        f"8 (861) 123-45-67",
        reply_markup=MAIN_MENU
    )
    await state.finish()

@dp.callback_query_handler(text="cancel_booking", state="*")
async def cancel_booking(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text("❌ Бронь отменена.", reply_markup=MAIN_MENU)
    await state.finish()

# ТВОЙ СТАРЫЙ КОД ЗАКАЗОВ (без изменений)
@dp.message_handler()
async def handle_order(message: types.Message):
    text = message.text.lower()
    
    if 'кофе' in text or '☕' in text:
        await message.reply(
            "☕ **Заказ принят**\n"
            "💰 Кофе классический — 200₽\n\n"
            "_✅ Подтвердить заказ?_",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton('✅ Подтвердить'), KeyboardButton('❌ Отмена')]
            ], resize_keyboard=True),
            parse_mode='Markdown'
        )
    elif 'чай' in text or '🍵' in text:
        await message.reply(
            "🍵 **Заказ принят**\n"
            "💰 Чай — 150₽\n\n"
            "_✅ Подтвердить заказ?_",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton('✅ Подтвердить'), KeyboardButton('❌ Отмена')]
            ], resize_keyboard=True),
            parse_mode='Markdown'
        )
    elif 'пирог' in text or '🥧' in text:
        await message.reply(
            "🥧 **Заказ принят**\n"
            "💰 Пирог яблочный — 100₽\n\n"
            "_✅ Подтвердить заказ?_",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton('✅ Подтвердить'), KeyboardButton('❌ Отмена')]
            ], resize_keyboard=True),
            parse_mode='Markdown'
        )
    else:
        await message.reply(
            "❓ **Не понял заказ**\n\n"
            "_Напиши:_\n"
            "• `кофе`\n"
            "• `чай`\n"
            "• `пирог`\n\n"
            "или выбери кнопку ☝️",
            reply_markup=MAIN_MENU,
            parse_mode='Markdown'
        )

# WEBHOOK (без изменений)
import os
from aiogram import executor

WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://chatbotify-2tjd.onrender.com{WEBHOOK_PATH}"

async def on_startup(dp):
    bot = Bot(token=TOKEN)
    await bot.set_webhook(WEBHOOK_URL)
    print("✅ Webhook activated!")

if __name__ == '__main__':
    executor.start_webhook(
        dp,
        WEBHOOK_PATH,
        on_startup=on_startup,
        host="0.0.0.0", 
        port=int(os.getenv('PORT', 10000))
    )
