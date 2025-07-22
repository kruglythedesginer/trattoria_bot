print("Бот запущен")
import asyncio
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import logging

# --- Конфигурация ---
API_TOKEN = '8129289958:AAHNaW7asvSS3tpZlvsBRq1lwyB71B7lZSY'
ADMINS = [6267452026]
DATABASE_NAME = 'tasks.db'

# --- Логирование ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# --- Инициализация БД ---
def init_db():
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS workers (
                user_id INTEGER PRIMARY KEY,
                username TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                creator_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_repeating BOOLEAN DEFAULT FALSE,
                cron_time TEXT,
                FOREIGN KEY (creator_id) REFERENCES workers (user_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_status (
                task_id INTEGER,
                worker_id INTEGER,
                status TEXT DEFAULT 'pending',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (task_id, worker_id),
                FOREIGN KEY (task_id) REFERENCES tasks (id),
                FOREIGN KEY (worker_id) REFERENCES workers (user_id)
            )
        """)
        conn.commit()

# --- Состояния ---
class TaskStates(StatesGroup):
    awaiting_task_text = State()
    awaiting_repeat_input = State()
    awaiting_daily_time = State()
    awaiting_weekly_day = State()
    awaiting_repeat_task_text = State()

# --- Клавиатуры ---
def task_reply_keyboard(task_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"done:{task_id}")],
        [InlineKeyboardButton(text="❌ Не выполнено", callback_data=f"fail:{task_id}")]
    ])

def admin_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Создать задачу", callback_data="create_task")],
        [InlineKeyboardButton(text="🔄 Создать повторяющуюся задачу", callback_data="create_repeat_task")],
        [InlineKeyboardButton(text="📊 Список задач", callback_data="list_tasks")]
    ])

def repeat_options_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕒 Каждый день", callback_data="repeat_daily")],
        [InlineKeyboardButton(text="📅 Каждую неделю", callback_data="repeat_weekly")]
    ])

def time_picker_keyboard():
    times = [f"{h:02d}:00" for h in range(9, 24)]
    rows = [[InlineKeyboardButton(text=t, callback_data=f"time_{t}")] for t in times]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def weekday_picker_keyboard():
    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    days_map = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=day, callback_data=f"weekday_{code}")]
        for day, code in zip(days, days_map)
    ])

# --- Команды и хендлеры ---
@dp.message(Command("start"))
async def start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or str(user_id)
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO workers (user_id, username) VALUES (?, ?)", (user_id, username))
        conn.commit()
    if user_id in ADMINS:
        await message.answer("👋 Добро пожаловать, руководитель!", reply_markup=admin_menu_keyboard())
    else:
        await message.answer("👋 Вы добавлены как сотрудник. Ожидайте задач!")

@dp.callback_query(F.data == "create_task")
async def create_task_handler(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите текст задачи:")
    await state.set_state(TaskStates.awaiting_task_text)
    await callback.answer()

@dp.message(TaskStates.awaiting_task_text)
async def process_task_text(message: Message, state: FSMContext):
    task_text = message.text
    creator_id = message.from_user.id
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO tasks (text, creator_id) VALUES (?, ?)", (task_text, creator_id))
        task_id = cursor.lastrowid
        cursor.execute("SELECT user_id FROM workers WHERE user_id NOT IN (?)", (ADMINS[0],))
        workers = cursor.fetchall()
        for worker in workers:
            cursor.execute("INSERT INTO task_status (task_id, worker_id) VALUES (?, ?)", (task_id, worker[0]))
        conn.commit()
    for worker in workers:
        try:
            await bot.send_message(worker[0], f"📌 Новая задача:\n{task_text}", reply_markup=task_reply_keyboard(task_id))
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
    await message.answer("✅ Задача создана!")
    await state.clear()

@dp.callback_query(F.data.startswith("done:") | F.data.startswith("fail:"))
async def handle_task_response(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    action, task_id = callback.data.split(":")
    task_id = int(task_id)
    status = "completed" if action == "done" else "failed"
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""UPDATE task_status SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ? AND worker_id = ?""", (status, task_id, user_id))
        cursor.execute("""SELECT t.text, t.creator_id, w.username FROM tasks t JOIN workers w ON w.user_id = ? WHERE t.id = ?""", (user_id, task_id))
        task_data = cursor.fetchone()
        conn.commit()
    if task_data:
        task_text, creator_id, username = task_data
        status_text = "выполнена" if status == "completed" else "не выполнена"
        await callback.message.edit_text(f"📌 Задача:\n{task_text}\n\n🔄 Статус: {status_text} ({username})")
        try:
            await bot.send_message(creator_id, f"ℹ️ Сотрудник @{username} отметил задачу как {status_text}:\n{task_text}")
        except Exception as e:
            logger.error(f"Не удалось уведомить админа: {e}")
    await callback.answer(f"Статус обновлен: {status_text}")

@dp.callback_query(F.data == "create_repeat_task")
async def start_repeat_task(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Выберите тип повторения:", reply_markup=repeat_options_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("repeat_"))
async def choose_repeat_type(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "repeat_daily":
        await state.update_data(repeat_type="daily")
        await callback.message.answer("Выберите время:", reply_markup=time_picker_keyboard())
        await state.set_state(TaskStates.awaiting_daily_time)
    elif callback.data == "repeat_weekly":
        await state.update_data(repeat_type="weekly")
        await callback.message.answer("Выберите день недели:", reply_markup=weekday_picker_keyboard())
        await state.set_state(TaskStates.awaiting_weekly_day)
    await callback.answer()

@dp.callback_query(F.data.startswith("weekday_"))
async def choose_weekday(callback: types.CallbackQuery, state: FSMContext):
    weekday = callback.data.replace("weekday_", "")
    await state.update_data(weekday=weekday)
    await callback.message.answer("Теперь выберите время:", reply_markup=time_picker_keyboard())
    await state.set_state(TaskStates.awaiting_daily_time)
    await callback.answer()

@dp.callback_query(F.data.startswith("time_"))
async def choose_time(callback: types.CallbackQuery, state: FSMContext):
    time_str = callback.data.replace("time_", "")
    await state.update_data(cron_time=time_str)
    await callback.message.answer("Введите текст повторяющейся задачи:")
    await state.set_state(TaskStates.awaiting_repeat_task_text)
    await callback.answer()

@dp.message(TaskStates.awaiting_repeat_task_text)
async def save_repeat_task(message: Message, state: FSMContext):
    data = await state.get_data()
    text = message.text
    creator_id = message.from_user.id
    cron_time = data.get("cron_time")
    weekday = data.get("weekday")
    is_weekly = data.get("repeat_type") == "weekly"
    hour, minute = map(int, cron_time.split(":"))
    if is_weekly:
        trigger = CronTrigger(day_of_week=weekday, hour=hour, minute=minute)
        cron_expr = f"{minute} {hour} * * {weekday}"
    else:
        trigger = CronTrigger(hour=hour, minute=minute)
        cron_expr = f"{minute} {hour} * * *"
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO tasks (text, creator_id, is_repeating, cron_time) VALUES (?, ?, ?, ?)", (text, creator_id, True, cron_expr))
        task_id = cursor.lastrowid
        conn.commit()
    async def send_repeating_task():
        with sqlite3.connect(DATABASE_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM workers WHERE user_id NOT IN (?)", (ADMINS[0],))
            workers = cursor.fetchall()
            for worker in workers:
                cursor.execute("INSERT OR IGNORE INTO task_status (task_id, worker_id) VALUES (?, ?)", (task_id, worker[0]))
            conn.commit()
        for worker in workers:
            try:
                await bot.send_message(worker[0], f"🔁 Повторяющаяся задача:\n{text}", reply_markup=task_reply_keyboard(task_id))
            except Exception as e:
                logger.error(f"Ошибка при рассылке повторной задачи: {e}")
    scheduler.add_job(send_repeating_task, trigger)
    await message.answer("✅ Повторяющаяся задача создана!")
    await state.clear()

# --- Запуск ---
async def on_startup():
    init_db()
    scheduler.start()
    logger.info("Бот запущен")

if __name__ == "__main__":
    dp.startup.register(on_startup)
    asyncio.run(dp.start_polling(bot))