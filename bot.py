import os
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
import sqlite3

load_dotenv()

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Подключение к базе данных SQLite
conn = sqlite3.connect('photo_stats.db', check_same_thread=False)
cursor = conn.cursor()

# Миграция базы данных: добавление столбца chat_id, если он еще не существует
cursor.execute('PRAGMA table_info(photos)')
columns = cursor.fetchall()
column_names = [col[1] for col in columns]

if 'chat_id' not in column_names:
    logger.info("Добавляем столбец chat_id в таблицу photos")
    cursor.execute('ALTER TABLE photos ADD COLUMN chat_id INTEGER')
    conn.commit()

# Флаг для проверки, была ли команда /start выполнена для каждой группы
start_executed = {}

# Константы для состояний диалога
PERIOD_REQUEST = 0


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in start_executed:
        start_executed[chat_id] = False

    try:
        if not start_executed[chat_id]:
            await update.message.reply_text(
                "Привет! Я буду отслеживать количество отправленных фото в этой группе."
            )
            start_executed[chat_id] = True
        else:
            await update.message.reply_text("Привет! Я уже отслеживаю количество отправленных фото в этой группе.")
    except Exception as e:
        logger.error(f"Ошибка в команде /start для чата {chat_id}: {e}")
        await update.message.reply_text("❌ Произошла ошибка при выполнении команды /start")


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        user = update.message.from_user
        if user.is_bot:
            return

        cursor.execute(
            'INSERT INTO photos (chat_id, user_id, timestamp) VALUES (?, ?, ?)',
            (chat_id, user.id, int(datetime.now().timestamp()))
        )
        conn.commit()
        logger.info(f"Фото от {user.full_name} сохранено в чате {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка обработки фото в чате {chat_id}: {e}")


async def show_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Введите период в днях для анализа:")
    return PERIOD_REQUEST


async def process_period(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        chat_id = update.effective_chat.id
        period_days = int(update.message.text)
        if period_days <= 0:
            await update.message.reply_text("Период должен быть больше 0 дней")
            return PERIOD_REQUEST

        time_threshold = int((datetime.now() - timedelta(days=period_days)).timestamp())

        # Получаем статистику из БД для данной группы
        cursor.execute('''
            SELECT user_id, COUNT(*) as count 
            FROM photos 
            WHERE chat_id = ? AND timestamp >= ? 
            GROUP BY user_id 
            ORDER BY count DESC 
            LIMIT 5
        ''', (chat_id, time_threshold))

        top_users = cursor.fetchall()
        if not top_users:
            await update.message.reply_text(f"📭 За последние {period_days} дней фото не отправлялись")
            return ConversationHandler.END

        response = [f"🏆 Топ участников за {period_days} дней:"]
        for idx, (user_id, count) in enumerate(top_users, 1):
            try:
                user = await context.bot.get_chat_member(chat_id, user_id)
                name = user.user.mention_markdown()
            except Exception as e:
                logger.error(f"Ошибка при получении информации о пользователе {user_id} в чате {chat_id}: {e}")
                name = f"Пользователь {user_id}"

            response.append(f"{idx}. {name} — {count} фото")

        await update.message.reply_text("\n".join(response), parse_mode="Markdown")

    except ValueError:
        await update.message.reply_text("❌ Введите корректное число дней")
        return PERIOD_REQUEST
    except Exception as e:
        logger.error(f"Ошибка обработки периода в чате {chat_id}: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка при обработке запроса")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("🚫 Операция отменена")
    return ConversationHandler.END


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("Токен бота не найден!")
        return

    application = ApplicationBuilder().token(token).concurrent_updates(True).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("top", show_top)],
        states={
            PERIOD_REQUEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_period)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    application.add_error_handler(lambda update, context: logger.error(context.error))

    application.run_polling()


if __name__ == "__main__":
    main()