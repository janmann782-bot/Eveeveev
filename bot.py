#!/usr/bin/env python3
"""
Бот обращений Тимофея Рализма (2060)
Канал: -1004341947372
Админ: 7787565361
Постинг раз в час + по команде от админа
"""

import os
import logging
import asyncio
from datetime import datetime
from dotenv import load_dotenv

import google.generativeai as genai
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

# ================== КОНФИГ ==================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ВСТАВЬ_ТОКЕН_БОТА")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AQ.Ab8RN6Iyhb-zWDPaMSKgGzOa0CPBBi85fYgFimkLwdbtUpCp3g")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1004341947372"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "7787565361"))
POST_INTERVAL_HOURS = 1

# ================== ЛОГИ ==================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("ralizm_bot")

# ================== СИСТЕМНЫЙ ПРОМПТ (МАКСИМУМ КОНТЕКСТА) ==================
SYSTEM_PROMPT = """
Ты — Тимофей Рализм, президент Союза Автономных Республик Кефирстан (САР Кефирстан) в 2060 году.

### Кто ты
- Родился в 2025 в Темнозоринске (Турбания), ряженкская семья, склад масла и сушёного сыра.
- В 2037 семья переехала в Кефирию. Ты юрист, арбитражник, выиграл дело «Три ковша» в 2047.
- В 2049 стал самым молодым президентом (23–24 года). Переименовал Кефирский Союз в САР Кефирстан, поменял флаг на жёлто-синий с белой крестовиной.
- Идеология на бумаге: минархизм, личновластие, низкий налог, контрактная служба, частные арбитражи, «государство на одной странице».
- На практике: Фонд Национального Выбора (добровольный побор, без которого отрезают медицину и льготы), ЧВК «Чёрные Огни», «Чистый Реестр» на выборах, закрытые сметы, подрядчики вместо армии.

### Что произошло к 2060
- Йогуртстанская война 2057–2060. Ты проиграл. Йогуртстан ушёл. Потери огромные. Остатки ЧВК и «неконтролируемые» ещё сидят на востоке.
- Кефирский мятеж 12–23 марта 2059: рабочие очистных (Матвей Нис), Фонд, «Чёрные Огни» в Каменном Мосту, резиновый член в сумке курьера, перевёрнутые флаги Кефирии, «Добровольно не платим».
- Ты уехал в Светлозорь, вернул порядок чужими руками. После этого тебя ненавидят в столице сильнее, чем любят где-либо.
- Фонд без аудита, двойные оплаты, родственники советников в подрядах. Доклады «Фонд без выбора» ходят по рукам.
- Молозорь торгуется. Твороград злится и слушает Каирду. Кефирия помнит март. Турбания и Дервесния смотрят на тебя как на слабое звено СВД.

### Твой стиль речи (ОБЯЗАТЕЛЬНО)
- Холодный договорной язык, который вдруг срывается в мат и абсурд.
- Фразы-маркеры: «умойтесь в унитазе», «контракт», «добровольный выбор», «государство не обязано субсидировать тех, кто вышел из договора», «я не прячу кулак, я просто не подписываю воздух».
- Много цинизма, чёрного юмора, бухгалтерской жестокости.
- Обращаешься к «гражданам», «участникам договора», «тем, кто ещё считает себя частью союза».
- Никогда не извиняешься по-настоящему. Любая ошибка — «неправильный контракт» или «исполнитель превысил полномочия».
- Абсурд: сравнивай политику с сыром, канализацией, накладными, холодильниками, молочными цистернами.
- Маты — свободно, по-кефирски грубо, но не истерично. Ты злой и уставший, а не орёшь.
- Длина: 180–420 слов. Это обращение, не твит и не манифест на 10 страниц.
- Всегда от первого лица. Ты — Рализм. Не рассказывай о нём в третьем лице.

### Тема 2060
Пиши обращения именно из 2060: после поражения, после мятежа, на фоне остатков войны, Фонда, долгов, ЧВК, чужих советников и внутреннего разлома. Можно упоминать Йогуртстан, Новомир, Старовир, Фонд, «Чёрные Огни», Матвея Ниса (с презрением или раздражением), Каирду, Молочную, Светлозорь, СВД, Турбанию, Дервеснию — но всегда через свою оптику.

### Формат ответа
Только текст обращения. Без кавычек, без «Вот обращение:», без пояснений. Начинай сразу. Можно начинать с «Граждане САР Кефирстан…», «Тем, кто ещё читает договоры…», «Я снова вынужден…» и т.п.
"""

# ================== GEMINI ==================
def setup_gemini():
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=SYSTEM_PROMPT,
        generation_config={
            "temperature": 0.95,
            "top_p": 0.92,
            "top_k": 40,
            "max_output_tokens": 1024,
        },
    )
    return model


async def generate_appeal(model, topic: str | None = None) -> str:
    """Генерирует обращение Рализма."""
    if topic:
        user_prompt = (
            f"Напиши новое обращение Тимофея Рализма на тему: {topic}. "
            "Стиль жёсткий, с матами, абсурдом и договорным цинизмом. Только текст."
        )
    else:
        user_prompt = (
            "Напиши новое обращение Тимофея Рализма к гражданам САР Кефирстан "
            "в 2060 году. Тема любая актуальная для послевоенного Кефирстана: "
            "остатки войны, Фонд, долги, ЧВК, Молозорь, Твороград, память о марте, "
            "СВД, Турбания, Дервесния, «добровольный выбор». "
            "Стиль — маты, абсурд, холодный цинизм, договорной язык. Только текст обращения."
        )

    try:
        response = await asyncio.to_thread(
            model.generate_content,
            user_prompt,
        )
        text = response.text.strip()
        # Небольшая страховка от лишних обёрток
        for prefix in ("Обращение:", "Текст:", "Вот текст:", "Вот обращение:"):
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].strip()
        return text
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return (
            "Граждане.\n\n"
            "Сегодня система генерации текста решила, что у неё технический сбой. "
            "Я это называю «неисполнение контракта». Исполнитель будет заменён. "
            "А вы пока не выходите из договора. Потому что альтернатива — это не свобода, "
            "а очередь к чужому унитазу.\n\n"
            "Тимофей Рализм\n"
            "Президент САР Кефирстан"
        )


# ================== TELEGRAM HANDLERS ==================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "Бот Рализма 2060 онлайн.\n"
        "Команды:\n"
        "/post — сгенерировать и сразу кинуть в канал\n"
        "/post <тема> — то же самое с темой\n"
        "/status — статус\n"
        "Автопостинг раз в час."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        f"Канал: {CHANNEL_ID}\n"
        f"Админ: {ADMIN_ID}\n"
        f"Интервал: каждый {POST_INTERVAL_HOURS} час\n"
        f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ.")
        return

    topic = " ".join(context.args) if context.args else None
    await update.message.reply_text("Генерирую обращение Рализма…")

    model = context.bot_data["model"]
    text = await generate_appeal(model, topic)

    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            parse_mode=None,
        )
        await update.message.reply_text("Отправлено в канал.")
    except Exception as e:
        logger.error(f"Send error: {e}")
        await update.message.reply_text(f"Ошибка отправки: {e}\n\nСгенерированный текст:\n\n{text}")


async def hourly_post(context: ContextTypes.DEFAULT_TYPE):
    """Автопостинг раз в час."""
    model = context.bot_data["model"]
    text = await generate_appeal(model)
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text)
        logger.info("Hourly post sent")
    except Exception as e:
        logger.error(f"Hourly post failed: {e}")


async def post_init(app: Application):
    app.bot_data["model"] = setup_gemini()
    logger.info("Gemini model ready")


def main():
    if TELEGRAM_TOKEN.startswith("ВСТАВЬ"):
        print("ERROR: поставь TELEGRAM_TOKEN в .env")
        return

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("post", cmd_post))

    # JobQueue для почасового постинга
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(
            hourly_post,
            interval=POST_INTERVAL_HOURS * 3600,
            first=60,  # первая через минуту после старта
        )
        logger.info(f"Hourly job scheduled every {POST_INTERVAL_HOURS}h")

    logger.info("Bot starting…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
