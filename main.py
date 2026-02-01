import logging
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

logging.basicConfig(level=logging.INFO)

# Токен из .env
load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

# Главное меню
MAIN_MENU = ReplyKeyboardMarkup(resize_keyboard=True)
MAIN_MENU.add(KeyboardButton('☕ Кофе 200₽'), KeyboardButton('🍵 Чай 150₽'))
MAIN_MENU.add(KeyboardButton('🥧 Пирог 100₽'), KeyboardButton('🛒 Оформить заказ'))
MAIN_MENU.add(KeyboardButton('❓ Помощь'))

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    await message.reply(
        "👋 Привет!\n\n☕️ **МЕНЮ КАФЕ BOTIFY**\n\n"
        "☕ Кофе 200₽\n"
        "🍵 Чай 150₽\n"
        "🥧 Пирог 100₽\n\n"
        "_Выбери кнопку или напиши заказ_",
        reply_markup=MAIN_MENU,
        parse_mode='Markdown'
    )

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

if __name__ == '__main__':
    print("🚀 ChatBotify aiogram LIVE!")
import os
from aiogram import executor

WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://chatbotify.onrender.com{WEBHOOK_PATH}"

async def on_startup(dp):
    bot = Bot(token=BOT_TOKEN)
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
