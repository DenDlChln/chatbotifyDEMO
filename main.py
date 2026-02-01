import logging
import os
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

logging.basicConfig(level=logging.INFO)

# Токен из .env
load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

# Главное меню
MAIN_MENU = ReplyKeyboardMarkup(resize_keyboard=True)
MAIN_MENU.row(KeyboardButton('☕ Кофе 200₽'), KeyboardButton('📋 Бронь столика'))
MAIN_MENU.row(KeyboardButton('🍵 Чай 150₽'), KeyboardButton('🛒 Оформить заказ'))
MAIN_MENU.row(KeyboardButton('❓ Помощь'))

# СОСТОЯНИЯ БРОНИРОВАНИЯ (текстовый ввод)
class BookingForm(StatesGroup):
    waiting_datetime = State()
    waiting_people = State()
    waiting_name = State()
    waiting_phone = State()

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    await message.reply(
        "👋 Привет!\n\n☕️ **МЕНЮ КАФЕ BOTIFY**\n\n"
        "☕ Кофе 200₽\n🍵 Чай 150₽\n🥧 Пирог 100₽\n📋 Бронь столика\n\n"
        "_Выбери кнопку или напиши заказ_",
        reply_markup=MAIN_MENU,
        parse_mode='Markdown'
    )

# 🆕 БРОНИРОВАНИЕ - ТЕКСТОВЫЙ ВВОД (100% стабильно)
@dp.message_handler(lambda message: message.text == '📋 Бронь столика')
async def book_table_start(message: types.Message, state: FSMContext):
    await message.reply(
        "📅 **Введите дату и время**:\n"
        "`ДД.ММ ЧЧ:ММ` (пример: `15.02 19:00`)\n\n"
        "💡 Брони с 18:00-22:00\n"
        "💡 Сегодня/завтра автоматически",
        parse_mode='Markdown'
    )
    await BookingForm.waiting_datetime.set()

@dp.message_handler(state=BookingForm.waiting_datetime)
async def process_datetime(message: types.Message, state: FSMContext):
    text = message.text.strip()
    pattern = r'(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{1,2})'
    
    match = re.match(pattern, text)
    if not match:
        await message.reply(
            "❌ **Неверный формат!**\n"
            "`15.02 19:00` или `15.02 20:00`\n\n"
            "Попробуйте снова:",
            parse_mode='Markdown'
        )
        return
    
    try:
        day, month, hour, minute = map(int, match.groups())
        
        # Сегодня или завтра
        now = datetime.now()
        booking_date = now.replace(day=day, month=month, hour=hour, minute=minute, second=0, microsecond=0)
        
        if booking_date <= now:
            booking_date = booking_date + timedelta(days=1)
        
        # Валидация времени (18:00-22:00)
        if not (18 <= hour <= 22) or minute not in [0, 30]:
            await message.reply(
                "❌ **Неверное время!**\n"
                "Доступно: 18:00, 18:30, 19:00... 22:00\n\n"
                "Пример: `15.02 19:00`",
                parse_mode='Markdown'
            )
            return
        
        await state.update_data(datetime=booking_date)
        
        # Кнопки для людей
        people_kb = ReplyKeyboardMarkup(
            resize_keyboard=True, 
            one_time_keyboard=True
        )
        people_kb.row('1-2', '3-4')
        people_kb.row('5+', '❌ Отмена')
        
        await message.reply(
            f"✅ **{booking_date.strftime('📅 %d.%m.%Y %H:%M')}\n\n**👥 Сколько человек?**",
            reply_markup=people_kb,
            parse_mode='Markdown'
        )
        await BookingForm.waiting_people.set()
        
    except Exception:
        await message.reply(
            "❌ **Ошибка даты**. Формат: `15.02 19:00`",
            parse_mode='Markdown'
        )

@dp.message_handler(state=BookingForm.waiting_people)
async def process_people(message: types.Message, state: FSMContext):
    text = message.text
    
    if text == '❌ Отмена':
        await message.reply("❌ Бронь отменена.", reply_markup=MAIN_MENU)
        await state.finish()
        return
    
    people_map = {'1-2': 2, '3-4': 4, '5+': 6}
    people = people_map.get(text, 2)
    
    data = await state.get_data()
    booking_time = data['datetime'].strftime('%d.%m.%Y %H:%M')
    
    await message.reply(
        f"✅ **Бронь подтверждена!**\n\n"
        f"📅 {booking_time}\n"
        f"👥 {people} человек\n\n"
        f"📞 **Подтверждение по телефону:**\n"
        f"**8 (861) 123-45-67**\n\n"
        f"🎉 Спасибо за выбор CafeBotify! ☕",
        reply_markup=MAIN_MENU,
        parse_mode='Markdown'
    )
    await state.finish()

# Заказы (без изменений)
@dp.message_handler()
async def handle_order(message: types.Message):
    text = message.text.lower()
    
    if 'кофе' in text or '☕' in text:
        await message.reply(
            "☕ **Заказ принят**\n💰 Кофе классический — 200₽\n\n_✅ Подтвердить заказ?_",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton('✅ Подтвердить'), KeyboardButton('❌ Отмена')]
            ], resize_keyboard=True),
            parse_mode='Markdown'
        )
    elif 'чай' in text or '🍵' in text:
        await message.reply(
            "🍵 **Заказ принят**\n💰 Чай — 150₽\n\n_✅ Подтвердить заказ?_",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton('✅ Подтвердить'), KeyboardButton('❌ Отмена')]
            ], resize_keyboard=True),
            parse_mode='Markdown'
        )
    elif 'пирог' in text or '🥧' in text:
        await message.reply(
            "🥧 **Заказ принят**\n💰 Пирог яблочный — 100₽\n\n_✅ Подтвердить заказ?_",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton('✅ Подтвердить'), KeyboardButton('❌ Отмена')]
            ], resize_keyboard=True),
            parse_mode='Markdown'
        )
    else:
        await message.reply(
            "❓ **Не понял заказ**\n\n_Напиши:_\n• `кофе`\n• `чай`\n• `пирог`\n\n_или выбери кнопку ☝️_",
            reply_markup=MAIN_MENU,
            parse_mode='Markdown'
        )

# WEBHOOK для Render
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

