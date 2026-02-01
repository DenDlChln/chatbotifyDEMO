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

logging.basicConfig(level=logging.INFO)

load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')
storage = MemoryStorage()
bot = Bot(token=TOKEN)
dp = Dispatcher(bot, storage=storage)

MAIN_MENU = ReplyKeyboardMarkup(resize_keyboard=True)
MAIN_MENU.row(KeyboardButton('☕ Кофе 200₽'), KeyboardButton('📋 Бронь столика'))
MAIN_MENU.row(KeyboardButton('🍵 Чай 150₽'), KeyboardButton('🛒 Оформить заказ'))
MAIN_MENU.row(KeyboardButton('❓ Помощь'))

# ✅ СОСТОЯНИЯ ЗАКАЗА
class OrderForm(StatesGroup):
    waiting_quantity = State()
    waiting_confirm = State()

class BookingForm(StatesGroup):
    waiting_datetime = State()
    waiting_people = State()

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    await message.reply("👋 **CafeBotify** ☕\nВыберите:", reply_markup=MAIN_MENU, parse_mode='Markdown')

# 🆕 ЗАКАЗЫ - FSM ЛОГИКА
@dp.message_handler(lambda m: m.text in ['☕ Кофе 200₽', '🍵 Чай 150₽'])
async def start_order(message: types.Message, state: FSMContext):
    items = {'☕ Кофе 200₽': 'Кофе (200₽)', '🍵 Чай 150₽': 'Чай (150₽)'}
    item = items[message.text]
    
    await state.update_data(item=item, price=200 if 'Кофе' in item else 150)
    await message.reply(
        f"☕ **{item}**\n\n"
        "📊 **Сколько порций?**\n"
        "`1`, `2`, `3+`",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup([
            ['1', '2', '3+'], ['❌ Отмена']
        ], resize_keyboard=True, one_time_keyboard=True)
    )
    await OrderForm.waiting_quantity.set()

@dp.message_handler(state=OrderForm.waiting_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    if message.text == '❌ Отмена':
        await message.reply("❌ Заказ отменён ☕", reply_markup=MAIN_MENU)
        await state.finish()
        return
    
    qty = {'1': 1, '2': 2, '3+': 3}.get(message.text, 1)
    data = await state.get_data()
    
    total = data['price'] * qty
    await state.update_data(quantity=qty, total=total)
    
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row('✅ Подтвердить', '❌ Отмена')
    
    await message.reply(
        f"📋 **Ваш заказ:**\n"
        f"{data['item']} × {qty}\n"
        f"💰 Итого: {total}₽\n\n"
        "**Подтвердить заказ?**",
        reply_markup=kb,
        parse_mode='Markdown'
    )
    await OrderForm.waiting_confirm.set()

@dp.message_handler(state=OrderForm.waiting_confirm)
async def confirm_order(message: types.Message, state: FSMContext):
    if message.text == '❌ Отмена':
        await message.reply("❌ Заказ отменён ☕", reply_markup=MAIN_MENU)
        await state.finish()
        return
    
    data = await state.get_data()
    await message.reply(
        f"🎉 **ЗАКАЗ ПРИНЯТ!**\n\n"
        f"📋 {data['item']} × {data['quantity']}\n"
        f"💰 {data['total']}₽\n\n"
        f"⏰ Готовим! Подходите к стойке ☕\n\n"
        f"**CafeBotify**",
        reply_markup=MAIN_MENU,
        parse_mode='Markdown'
    )
    await state.finish()

# БРОНИРОВАНИЕ (без изменений - РАБОТАЕТ)
@dp.message_handler(lambda m: m.text == '📋 Бронь столика')
async def book_start(message: types.Message, state: FSMContext):
    await message.reply(
        "📅 **Дата время:**\n"
        "`15.02 19:00` ← ТОЧНО!\n"
        "18:00-22:00 (00/30 мин)",
        parse_mode='Markdown'
    )
    await BookingForm.waiting_datetime.set()

@dp.message_handler(state=BookingForm.waiting_datetime)
async def parse_datetime(message: types.Message, state: FSMContext):
    text = message.text.strip()
    match = re.match(r'^(\d{1,2})\.(\d{1,2})\s+(\d{2}):(\d{2})$', text)
    if not match:
        await message.reply("❌ **15.02 19:00** ← ТОЧНО!", parse_mode='Markdown')
        return
    
    day, month, hour, minute = map(int, match.groups())
    now = datetime.now()
    
    try:
        booking_dt = now.replace(day=day, month=month, hour=hour, minute=minute)
        if booking_dt <= now:
            booking_dt += timedelta(days=1)
        
        if hour < 18 or hour > 22 or minute not in [0, 30]:
            await message.reply("❌ **18:00, 18:30...22:00**", parse_mode='Markdown')
            return
        
        await state.update_data(dt=booking_dt)
        
        kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.row(KeyboardButton('1-2'), KeyboardButton('3-4'))
        kb.row(KeyboardButton('5+'), KeyboardButton('❌ Отмена'))
        
        await message.reply(
            f"✅ **{booking_dt.strftime('%d.%m %H:%M')}**\n\n👥 **Сколько человек?**",
            reply_markup=kb,
            parse_mode='Markdown'
        )
        await BookingForm.waiting_people.set()
        
    except:
        await message.reply("❌ **15.02 19:00**", parse_mode='Markdown')

@dp.message_handler(state=BookingForm.waiting_people)
async def finish_booking(message: types.Message, state: FSMContext):
    if message.text == '❌ Отмена':
        await message.reply("❌ Бронь отменена ☕", reply_markup=MAIN_MENU)
        await state.finish()
        return
    
    people_map = {'1-2': 2, '3-4': 4, '5+': 6}
    people = people_map.get(message.text, 2)
    data = await state.get_data()
    
    await message.reply(
        f"✅ **БРОНЬ ПОДТВЕРЖДЕНА!**\n\n"
        f"📅 {data['dt'].strftime('%d.%m %H:%M')}\n"
        f"👥 {people} человек\n\n"
        f"📞 **8 (861) 123-45-67**\n\n"
        f"🎉 **CafeBotify** ☕",
        reply_markup=MAIN_MENU,
        parse_mode='Markdown'
    )
    await state.finish()

# ЛОВИМ ВСЁ ОСТАЛЬНОЕ
@dp.message_handler()
async def catch_all(message: types.Message):
    await message.reply(
        "☕ **Меню:**\n"
        "☕ Кофе 200₽ | 🍵 Чай 150₽\n"
        "📋 Бронь столика\n\n"
        "_Выберите кнопку ☝️_",
        reply_markup=MAIN_MENU,
        parse_mode='Markdown'
    )

# WEBHOOK
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://chatbotify-2tjd.onrender.com{WEBHOOK_PATH}"

async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL)
    print("✅ CafeBotify LIVE - Заказы + Бронь!")

if __name__ == '__main__':
    executor.start_webhook(
        dp, WEBHOOK_PATH, on_startup=on_startup,
        host="0.0.0.0", port=int(os.getenv('PORT', 10000))
    )
