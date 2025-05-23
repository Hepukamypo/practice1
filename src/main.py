import os
import sqlite3
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.callback_data import CallbackData
from datetime import datetime, timedelta
from aiogram.types import ParseMode
import random
from dotenv import load_dotenv

load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

repeat_cb = CallbackData("repeat", "word", "action")

conn = sqlite3.connect("words.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS words (
    en TEXT PRIMARY KEY,
    ru TEXT,
    example TEXT
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS user_words (
    user_id INTEGER,
    word_en TEXT,
    last_repeat DATE,
    repeat_stage INTEGER,
    learned INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, word_en)
)''')
conn.commit()

class TestWord(StatesGroup):
    waiting_for_translation = State()
MAX_STAGE = 4

@dp.message_handler(commands=["test"])
async def cmd_test(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    cursor.execute("""
        SELECT w.en, w.ru FROM words w
        JOIN user_words uw ON w.en = uw.word_en
        WHERE uw.user_id = ? AND uw.learned = 0
    """, (user_id,))
    words = cursor.fetchall()

    if not words:
        await message.answer("❗ Нет слов для теста. Добавь их через /add и выучи через /learn.")
        return

    word = random.choice(words)
    await state.update_data(current_word_en=word[0], current_word_ru=word[1])
    await message.answer(f"👉 Переведи: <b>{word[0]}</b>", parse_mode=ParseMode.HTML)
    await TestWord.waiting_for_translation.set()

@dp.message_handler(commands=["stop"], state=TestWord.waiting_for_translation)
async def cmd_stop(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("🛑 Тест остановлен. Возвращайся, когда будешь готов продолжать!")

@dp.message_handler(state=TestWord.waiting_for_translation)
async def process_translation(message: types.Message, state: FSMContext):
    data = await state.get_data()
    correct = data["current_word_ru"].strip().lower()
    user_input = message.text.strip().lower()

    if user_input == correct:
        await message.answer("✅ Верно!")
        cursor.execute("""
            UPDATE user_words SET repeat_stage = repeat_stage + 1, last_repeat = CURRENT_DATE
            WHERE user_id = ? AND word_en = ?
        """, (message.from_user.id, data["current_word_en"]))
        conn.commit()
    else:
        await message.answer(f"❌ Неверно. Правильно: <b>{correct}</b>", parse_mode=ParseMode.HTML)

    cursor.execute("""
        SELECT w.en, w.ru FROM words w
        JOIN user_words uw ON w.en = uw.word_en
        WHERE uw.user_id = ? AND uw.learned = 0
    """, (message.from_user.id,))
    words = cursor.fetchall()

    if words:
        word = random.choice(words)
        await state.update_data(current_word_en=word[0], current_word_ru=word[1])
        await message.answer(f"👉 Переведи: <b>{word[0]}</b>", parse_mode=ParseMode.HTML)
    else:
        await message.answer("🎉 Все слова повторены! Отлично поработал!")
        await state.finish()

class AddWord(StatesGroup):
    English = State()
    Russian = State()
    Example = State()

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    await message.answer("Привет! Я помогу тебе учить английские слова.\n\nДоступные команды:\n/add — добавить слово\n/learn — получить 5 новых слов\n/repeat — повторить слова\n/stats — твой прогресс")

@dp.message_handler(commands=['add'])
async def cmd_add(message: types.Message):
    await message.answer("Введи английское слово:")
    await AddWord.English.set()

@dp.message_handler(state=AddWord.English)
async def process_en(message: types.Message, state: FSMContext):
    await state.update_data(en=message.text.strip())
    await message.answer("Теперь введи перевод:")
    await AddWord.Russian.set()

@dp.message_handler(state=AddWord.Russian)
async def process_ru(message: types.Message, state: FSMContext):
    await state.update_data(ru=message.text.strip())
    await message.answer("Теперь введи пример предложения:")
    await AddWord.Example.set()

@dp.message_handler(state=AddWord.Example)
async def process_example(message: types.Message, state: FSMContext):
    data = await state.get_data()
    en, ru, ex = data['en'], data['ru'], message.text.strip()
    cursor.execute("INSERT OR IGNORE INTO words (en, ru, example) VALUES (?, ?, ?)", (en, ru, ex))
    conn.commit()
    await message.answer(f"Слово '{en}' добавлено!")
    await state.finish()

@dp.message_handler(commands=['learn'])
async def learn_words(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT en FROM words WHERE en NOT IN (SELECT word_en FROM user_words WHERE user_id = ?)", (user_id,))
    new_words = cursor.fetchall()
    if not new_words:
        await message.answer("Нет новых слов для изучения. Добавь их через /add.")
        return
    to_learn = new_words[:5]
    for word in to_learn:
        en = word[0]
        cursor.execute("SELECT ru, example FROM words WHERE en = ?", (en,))
        ru, ex = cursor.fetchone()
        await message.answer(f"{en} — {ru}\nПример: {ex}")
        cursor.execute("INSERT INTO user_words (user_id, word_en, last_repeat, repeat_stage) VALUES (?, ?, ?, ?)",
                       (user_id, en, datetime.now().date(), 0))
    conn.commit()

@dp.message_handler(commands=['stats'])
async def stats(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT COUNT(*) FROM user_words WHERE user_id = ?", (user_id,))
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM user_words WHERE user_id = ? AND learned = 1", (user_id,))
    learned = cursor.fetchone()[0]
    await message.answer(f"Всего слов: {total}\nВыучено: {learned}")

intervals = [0, 1, 3, 7, 14]

def get_words_for_repeat(user_id):
    cursor.execute("""
        SELECT w.en, w.ru, uw.repeat_stage, uw.last_repeat 
        FROM user_words uw
        JOIN words w ON w.en = uw.word_en
        WHERE uw.user_id = ? AND uw.learned = 0
    """, (user_id,))
    words = cursor.fetchall()

    today = datetime.today().date()
    to_repeat = []

    for word in words:
        en, ru, stage, last = word
        if stage >= len(intervals):
            continue
        due_date = datetime.strptime(last, "%Y-%m-%d").date() + timedelta(days=intervals[stage])
        if today >= due_date:
            to_repeat.append((en, ru))

    return to_repeat

@dp.message_handler(commands=["repeat"])
async def repeat_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    words_to_repeat = get_words_for_repeat(user_id)
    if not words_to_repeat:
        await message.answer("Нет слов для повторения сегодня. Используй /learn чтобы добавить новые слова.")
        return

    await state.update_data(words_to_repeat=words_to_repeat, current_index=0)
    en, ru = words_to_repeat[0]
    cursor.execute("SELECT example FROM words WHERE en = ?", (en,))
    (ex,) = cursor.fetchone()

    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.insert(InlineKeyboardButton("Знаю", callback_data=repeat_cb.new(word=en, action="know")))
    keyboard.insert(InlineKeyboardButton("Не знаю", callback_data=repeat_cb.new(word=en, action="dont_know")))

    await message.answer(f"Повтори слово:\n\n{en} — {ru}\nПример: {ex}", reply_markup=keyboard)

@dp.callback_query_handler(repeat_cb.filter())
async def repeat_callback_handler(query: types.CallbackQuery, callback_data: dict, state: FSMContext):
    user_id = query.from_user.id
    word = callback_data["word"]
    action = callback_data["action"]

    if action == "know":
        cursor.execute("""
            UPDATE user_words
            SET repeat_stage = MIN(repeat_stage + 1, ?), last_repeat = ?
            WHERE user_id = ? AND word_en = ?
        """, (MAX_STAGE, datetime.now().date(), user_id, word))
    else:
        cursor.execute("""
            UPDATE user_words
            SET repeat_stage = 0, last_repeat = ?
            WHERE user_id = ? AND word_en = ?
        """, (datetime.now().date(), user_id, word))
    conn.commit()

    data = await state.get_data()
    words = data.get("words_to_repeat", [])
    index = data.get("current_index", 0) + 1

    if index < len(words):
        next_word_en, next_word_ru = words[index]
        cursor.execute("SELECT example FROM words WHERE en = ?", (next_word_en,))
        (ex,) = cursor.fetchone()

        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.insert(InlineKeyboardButton("Знаю", callback_data=repeat_cb.new(word=next_word_en, action="know")))
        keyboard.insert(InlineKeyboardButton("Не знаю", callback_data=repeat_cb.new(word=next_word_en, action="dont_know")))

        await state.update_data(current_index=index)
        await query.message.edit_text(f"Повтори слово:\n\n{next_word_en} — {next_word_ru}\nПример: {ex}", reply_markup=keyboard)
    else:
        await state.finish()
        await query.message.edit_text("🎉 Все слова повторены! Молодец!")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
