import asyncio
import logging
import re
import os
import random
from collections import Counter
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import aiosqlite

from wordcloud import WordCloud
from PIL import Image

API_TOKEN = "8573348886:AAF9dLh7fn2xrYRbfzujCaODRioIxenGgGs"  # <-- сюда вставьте токен бота

# сюда поставим ваш Telegram ID руководителя позже
ADMIN_ID = 574186003  

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

DB_PATH = "mood_bot.db"
BACKGROUND_IMAGE = "background.png"  # если файла нет, будет белый фон
MOTIVATIONS_PATH = "motivations.txt" # файл с мотивационными фразами
ALLOWED_USERNAMES = {
    "potashnik",
    "iamirishk",
    "vrchns",
    "aa_park",
    "leralapteva",
    "AllabaniLiza",
    "liskukushkina",
}



# ----------- ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ -----------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Пользователи, которые согласились на опросы
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY
            )
        """)
        # Ответы за конкретный день
        await db.execute("""
            CREATE TABLE IF NOT EXISTS answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                date TEXT,       -- YYYY-MM-DD
                text TEXT
            )
        """)
        await db.commit()


# ----------- ОБРАБОТЧИКИ КОМАНД -----------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    """
    Пользователь жмёт /start в ЛС.
    Добавляем его в список участников ежедневного опроса.
    """
    if message.chat.type != "private":
        await message.answer("Пожалуйста, напишите мне в личные сообщения, чтобы участвовать в опросе.")
        return

    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (user_id,)
        )
        await db.commit()

    await message.answer(
        "Привет, дружок! Изредка я буду тебе писать.\n"
        "Просто отвечай свободным текстом. Все ответы будут анонимны."
    )


@dp.message(Command("set_admin"))
async def cmd_set_admin(message: Message):
    """
    Команда для назначения руководителя.
    Отправьте /set_admin в ЛС боту с аккаунта руководителя.
    """
    global ADMIN_ID
    ADMIN_ID = message.from_user.id
    await message.answer(f"Вы назначены руководителем. Ваш ID сохранён: {ADMIN_ID}")


# ----------- ПРИЁМ ОТВЕТОВ ОТ СОТРУДНИКОВ -----------

@dp.message(F.chat.type == "private")
async def collect_answer(message: Message):
    """
    Любое текстовое сообщение в ЛС (кроме команд) считаем ответом на сегодняшний опрос.
    """
    if message.text.startswith("/"):
        # Игнорируем команды
        return

    user_id = message.from_user.id
    today = datetime.now().strftime("%Y-%m-%d")
    text = message.text.strip()

    async with aiosqlite.connect(DB_PATH) as db:
        # проверяем, что пользователь подписан
        cursor = await db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            await message.answer("Чтобы участвовать в опросе, сначала отправьте /start.")
            return

        await db.execute(
            "INSERT INTO answers (user_id, date, text) VALUES (?, ?, ?)",
            (user_id, today, text)
        )
        await db.commit()

    await message.answer("Спасибо! Твой ответ сохранён. В отчёте он будет анонимным.")


# ----------- РАССЫЛКА ВОПРОСОВ -----------

async def send_scheduled_questions():
    """
    Отправляет разный текст в зависимости от дня недели и времени.
    Дни недели: Monday=0, Tuesday=1, ..., Sunday=6
    """
    # Определяем день недели
    weekday = datetime.now().weekday()

    # Подбираем текст для конкретного дня
    if weekday == 0:
        # Понедельник
        text = "Привет, дружок, первый рабочий день закончился, как ты себя чувствуешь?"
    elif weekday == 2:
        # Среда
        text = "Привет, дружок, экватор рабочей недели за плечами, как твое настроение?"
    elif weekday == 4:
        # Пятница
        text = "Привет, дружок, вот и прошла неделя, желаю тебе хороших выходных, как ты себя чувствуешь?"
    else:
        # На всякий случай: если вызвали в другой день — ничего не делать
        return

    # Берём всех подписанных пользователей и шлём им выбранный текст
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM users")
        rows = await cursor.fetchall()

    for (user_id,) in rows:
        try:
            await bot.send_message(chat_id=user_id, text=text)
        except Exception as e:
            logging.warning(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
    
    async def send_morning_motivation():
    """
    Каждое утро отправляет всем пользователям утреннее сообщение
    с одной мотивационной фразой из файла motivations.txt.
    """
    # читаем все фразы из файла
    try:
        with open(MOTIVATIONS_PATH, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
    except FileNotFoundError:
        logging.error(f"Файл с мотивациями не найден: {MOTIVATIONS_PATH}")
        return

    if not lines:
        logging.warning("Файл с мотивациями пуст.")
        return

    # выбираем случайную фразу
    phrase = random.choice(lines)

    text = (
        "Доброе утро, дружочек-пирожочек, время 8 утра, пора вставать. "
        f"Ведь сегодня {phrase}"
    )

    # рассылаем всем пользователям
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM users")
        rows = await cursor.fetchall()

    for (user_id,) in rows:
        try:
            await bot.send_message(chat_id=user_id, text=text)
        except Exception as e:
            logging.warning(f"Не удалось отправить утреннее сообщение пользователю {user_id}: {e}")



# ----------- ГЕНЕРАЦИЯ ОБЛАКА СЛОВ -----------

def extract_words(text: str):
    """
    Разбор текста на слова.
    Можно улучшать (удалять стоп-слова, делать лемматизацию и т.д.).
    """
    words = re.findall(r"[А-Яа-яA-Za-z0-9ёЁ]+", text.lower())
    return words


def generate_wordcloud_image(text: str, output_path: str = "wordcloud.png"):
    """
    Создаём картинку-облако слов.
    - Если есть background.png — используем как маску/фон (упрощённый вариант).
    - Если нет — белый фон.
    """
    # Базовые параметры
    width = 1200
    height = 800

    # Пытаемся загрузить фон
    background = None
    try:
        bg_image = Image.open(BACKGROUND_IMAGE).convert("RGB")
        bg_image = bg_image.resize((width, height))
        background = bg_image
    except Exception:
        background = None  # не нашли фон — будет стандартный белый

    # Генерируем облако
    wc = WordCloud(
        width=width,
        height=height,
        background_color="white" if background is None else None,
        max_words=200,
        collocations=False,
        font_path=None  # при необходимости можно указать путь к шрифту, поддерживающему кириллицу
    )

    wc.generate(text)

    # Если есть фон — накладываем текст на фон
    if background is not None:
        # Рисуем облако в отдельное изображение
        cloud_img = wc.to_image().resize((width, height))
        # Накладываем облако на фон (упрощённо — смешиваем)
        final_img = Image.blend(background, cloud_img, alpha=0.7)
    else:
        final_img = wc.to_image()

    final_img.save(output_path)
    return output_path


# ----------- ОТЧЁТ РУКОВОДИТЕЛЮ -----------

async def send_daily_report():
    """
    Собираем все ответы за сегодня, генерируем картинку-облако и отправляем руководителю.
    """
    if ADMIN_ID == 0:
        logging.warning("ADMIN_ID не установлен, отчёт некому отправлять.")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT text FROM answers WHERE date = ?",
            (today,)
        )
        rows = await cursor.fetchall()

    if not rows:
        # Нет ответов
        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=f"Отчёт за {today}: ответов нет."
            )
        except Exception as e:
            logging.warning(f"Не удалось отправить отчёт руководителю: {e}")
        return

    # Объединяем все ответы
    all_text = " ".join(text for (text,) in rows)

    # Можно использовать весь текст как есть (фразы), 
    # либо разбить на слова.
    # Для классического облака слов оставим слова:
    words = extract_words(all_text)
    text_for_cloud = " ".join(words)

    # Генерируем картинку
    image_path = generate_wordcloud_image(text_for_cloud, output_path="wordcloud.png")

    # Отправляем картинку
    try:
        img = FSInputFile(image_path)
        await bot.send_photo(
            chat_id=ADMIN_ID,
            photo=img,
            caption=f"Облако настроений за {today} (анонимно)"
        )
    except Exception as e:
        logging.warning(f"Не удалось отправить картинку-отчёт руководителю: {e}")


# ----------- НАСТРОЙКА РАСПИСАНИЯ -----------

def setup_scheduler():
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")  # поменяйте часовой пояс, если нужен другой

    # Понедельник (0) — вопрос в 18:05
    scheduler.add_job(
        send_scheduled_questions,
        "cron",
        day_of_week="mon",
        hour=18,
        minute=5
    )

    # Среда (2) — вопрос в 18:05
    scheduler.add_job(
        send_scheduled_questions,
        "cron",
        day_of_week="wed",
        hour=18,
        minute=5
    )

    # Пятница (4) — вопрос в 17:05
    scheduler.add_job(
        send_scheduled_questions,
        "cron",
        day_of_week="fri",
        hour=17,
        minute=5
    )

    # Отчёт руководителю:
    # понедельник, среда, пятница в 18:45
    scheduler.add_job(
        send_daily_report,
        "cron",
        day_of_week="mon,wed,fri",
        hour=18,
        minute=45
    )
     # Утреннее сообщение каждый будний день (пн-пт) в 08:00
    scheduler.add_job(
        send_morning_motivation,
        "cron",
        day_of_week="mon-fri",
        hour=8,
        minute=0,
    )

    scheduler.start()


# ----------- MAIN -----------

async def main():
    await init_db()
    setup_scheduler()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
