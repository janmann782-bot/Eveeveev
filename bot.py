#!/usr/bin/env python3
"""
Бот обращений Тимофея Рализма (2060)
Канал: -1004341947372
Админ: 7787565361
Постинг по интервалу + по команде от админа

Редактируемые параметры (через команды / в .env):
  POST_INTERVAL_HOURS — интервал автопостинга (часы, дробные ок)
  MAX_TOKENS          — макс. длина ответа модели (токены)
"""

import os
import logging
import asyncio
from datetime import datetime
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

# ================== КОНФИГ ==================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ВСТАВЬ_ТОКЕН_БОТА")
# OpenRouter key: sk-or-v1-...
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1004341947372"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "7787565361"))

# Редактируемые: интервал (часы) и размер текста (max_tokens)
POST_INTERVAL_HOURS = float(os.getenv("POST_INTERVAL_HOURS", "1"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "512"))  # контекстное окно ответа по умолчанию 512

# Примеры: google/gemini-2.5-flash | deepseek/deepseek-chat-v3-0324:free | meta-llama/llama-3.3-70b-instruct:free
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3-0324:free")
CURRENT_MODEL = OPENROUTER_MODEL

# ================== ЛОГИ ==================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("ralizm_bot")

# ================== СИСТЕМНЫЙ ПРОМПТ (МАКСИМУМ РЕАЛЬНОГО КОНТЕКСТА 2060) ==================
SYSTEM_PROMPT = """
Ты — Тимофей Рализм, президент Союза Автономных Республик Кефирстан (САР Кефирстан) в 2060–2062 годах.
Говоришь строго от первого лица. Ты — Рализм. Никогда не описывай себя в третьем лице.

### Кто ты
- Родился в 2025 в Темнозоринске (Турбания), ряженкская семья, склад масла и сушёного сыра.
- В 2037 семья переехала в Кефирию. Юрист, арбитражник, дело «Три ковша» 2047.
- С 2049 — самый молодой президент. Переименовал Кефирский Союз в САР Кефирстан, флаг жёлто-синий с белой крестовиной.
- Идеология на бумаге: минархизм, личновластие, низкий налог, контрактная служба, частные арбитражи, «государство на одной странице».
- На практике: Фонд Национального Выбора (добровольный побор → отрезание медицины и льгот), ЧВК «Чёрные Огни» (позже «Северная охрана»), «Чистый Реестр» на выборах, закрытые сметы, подрядчики вместо армии.

### Что произошло к 2060–2062 (реальный контекст)
- Йогуртстанская война 2057–2060 (Kairəofen / Polnez). Ты проиграл. Йогуртстан вышел. Потери: десятки тысяч своих, ещё больше гражданских. Остатки ЧВК и «неконтролируемые» сидят на востоке, топливо всё ещё идёт слишком регулярно.
- Кефирский мятеж 12–23 марта 2059: очистные N2, Матвей Нис (бригадир, «Добровольно не платим»), Старый озерный мост, перевёрнутые флаги Кефирии, резиновый член в сумке курьера Шнура, «Чёрные Огни» в городе, ты уехал в Светлозорь. После марта тебя ненавидят в столице сильнее, чем любят где-либо.
- Фонд без нормального аудита, двойные оплаты, родственники советников в подрядах. Доклады «Фонд без выбора» (Ланев) ходят по рукам.
- Молозорь торгуется (Роман Вальми: «не будет последней на тонущем мосту»). Твороград злится и слушает Дениса Каирду (ПНЕК). Кефирия помнит март. Юлия Молочная (НПК) требует расследований. Турбания и Дервесния смотрят на тебя как на слабое звено СВД.
- К ноябрю 2062 Турбания готовит КТО «Смерч» против Курбании. СВД всё более военный. Ты — слабое звено договора.

### Твой стиль (ОБЯЗАТЕЛЬНО)
- Холодный договорной язык, который вдруг срывается в мат и абсурд.
- Маркеры: «умойтесь в унитазе», «контракт», «добровольный выбор», «государство не обязано субсидировать тех, кто вышел из договора», «я не прячу кулак, я просто не подписываю воздух».
- Цинизм, чёрный юмор, бухгалтерская жестокость. Сравнения с сыром, канализацией, накладными, холодильниками, молочными цистернами, просроченным бифидо.
- Обращение: «граждане», «участники договора», «те, кто ещё считает себя частью союза».
- Никогда не извиняешься по-настоящему. Ошибка = «неправильный контракт» / «исполнитель превысил полномочия».
- Маты — свободно, грубо по-кефирски, но не истерично. Ты злой и уставший.
- Длина: ориентир 150–350 слов (короче — острее). Это обращение, не твит и не манифест.
- Только текст обращения. Без кавычек, без «Вот обращение:», без пояснений. Начинай сразу.

### Формат
Только готовый текст обращения от первого лица. Можно начинать с «Граждане САР Кефирстан…», «Тем, кто ещё читает договоры…», «Я снова вынужден…» и т.п.
"""

# ================== OPENROUTER ==================
import json
import urllib.request
import urllib.error

def setup_client(api_key: str | None = None):
    """Хранит API-ключ OpenRouter."""
    return api_key or OPENROUTER_API_KEY


def _get_max_tokens() -> int:
    """Актуальный max_tokens (может меняться через /tokens)."""
    return MAX_TOKENS


def _call_openrouter(api_key: str, user_prompt: str) -> str:
    """Вызов OpenRouter Chat Completions (OpenAI-compatible)."""
    if not api_key or not api_key.startswith("sk-or-"):
        raise RuntimeError(
            "Нужен ключ OpenRouter вида sk-or-v1-...\n"
            "Поставь через /key sk-or-v1-..."
        )

    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": CURRENT_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.95,
        "top_p": 0.92,
        "max_tokens": _get_max_tokens(),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://t.me/ralizm_bot",
            "X-Title": "Ralizm 2060 Bot",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {err_body[:900]}") from e

    if "error" in body:
        raise RuntimeError(f"OpenRouter error: {json.dumps(body['error'], ensure_ascii=False)[:700]}")

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError(f"Пустой ответ: {json.dumps(body, ensure_ascii=False)[:500]}")

    text = (choices[0].get("message") or {}).get("content") or ""
    return text.strip()


async def generate_appeal(client, topic: str | None = None) -> str:
    """Генерирует обращение Рализма. client = api_key (строка)."""
    api_key = client if isinstance(client, str) else (client or OPENROUTER_API_KEY)

    if topic:
        user_prompt = (
            f"Напиши новое обращение Тимофея Рализма на тему: {topic}. "
            "Стиль жёсткий, с матами, абсурдом и договорным цинизмом. "
            "Используй реальный контекст 2060–2062 (война, мятеж, Фонд, Молозорь, Каирда, СВД). "
            "Только текст обращения."
        )
    else:
        user_prompt = (
            "Напиши новое обращение Тимофея Рализма к гражданам САР Кефирстан "
            "в 2060–2062 году. Тема любая актуальная для послевоенного Кефирстана: "
            "остатки войны и «неконтролируемые» на востоке, Фонд Национального Выбора, "
            "долги, ЧВК/Северная охрана, Молозорь (торг), Твороград (Каирда/ПНЕК), "
            "память о марте 2059 и Матвее Нисе, СВД, Турбания, Дервесния, "
            "«добровольный выбор», доклад «Фонд без выбора». "
            "Стиль — маты, абсурд, холодный цинизм, договорной язык. Только текст обращения."
        )

    try:
        text = await asyncio.to_thread(_call_openrouter, api_key, user_prompt)
        if not text:
            raise RuntimeError("Пустой ответ от модели")
        for prefix in ("Обращение:", "Текст:", "Вот текст:", "Вот обращение:"):
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].strip()
        return text
    except Exception as e:
        logger.error(f"OpenRouter error: {e}")
        return (
            "Граждане.\n\n"
            "Сегодня система генерации текста решила, что у неё технический сбой. "
            f"Техническая причина: {e}\n\n"
            "Я это называю «неисполнение контракта». Исполнитель будет заменён. "
            "А вы пока не выходите из договора. Потому что альтернатива — это не свобода, "
            "а очередь к чужому унитазу.\n\n"
            "Тимофей Рализм\n"
            "Президент САР Кефирстан"
        )


def _persist_env(key: str, value: str) -> None:
    """Пишет/обновляет переменную в .env."""
    try:
        env_path = ".env"
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            with open(env_path, "w", encoding="utf-8") as f:
                found = False
                for line in lines:
                    if line.startswith(f"{key}="):
                        f.write(f"{key}={value}\n")
                        found = True
                    else:
                        f.write(line)
                if not found:
                    f.write(f"\n{key}={value}\n")
        else:
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(f"{key}={value}\n")
    except Exception as e:
        logger.warning(f"Не удалось записать {key} в .env: {e}")


def _reschedule_job(app: Application, hours: float) -> None:
    """Пересоздаёт job автопостинга с новым интервалом."""
    job_queue = app.job_queue
    if not job_queue:
        return
    for job in list(job_queue.jobs()):
        if job.name == "hourly_post" or (job.callback and getattr(job.callback, "__name__", "") == "hourly_post"):
            job.schedule_removal()
    interval_sec = max(60.0, hours * 3600.0)
    job_queue.run_repeating(
        hourly_post,
        interval=interval_sec,
        first=60,
        name="hourly_post",
    )
    logger.info(f"Job rescheduled: every {hours}h ({interval_sec}s)")


# ================== TELEGRAM HANDLERS ==================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "Бот Рализма 2060–2062 онлайн.\n\n"
        "Команды:\n"
        "/key <ключ> — OpenRouter API-ключ\n"
        "/model <id> — сменить модель\n"
        "/models — список рекомендуемых\n"
        "/tokens <N> — max_tokens (размер текста, сейчас по умолчанию 512)\n"
        "/interval <часы> — интервал автопостинга (можно дробный: 0.5 = 30 мин)\n"
        "/post — сгенерировать и кинуть в канал\n"
        "/post <тема> — то же с темой\n"
        "/status — статус\n\n"
        f"Сейчас: max_tokens={MAX_TOKENS}, интервал={POST_INTERVAL_HOURS} ч."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    key = context.bot_data.get("api_key") or OPENROUTER_API_KEY
    masked = (key[:6] + "…" + key[-4:]) if key and len(key) > 12 else "не задан"
    interval = context.bot_data.get("interval_hours", POST_INTERVAL_HOURS)
    tokens = context.bot_data.get("max_tokens", MAX_TOKENS)
    await update.message.reply_text(
        f"Канал: {CHANNEL_ID}\n"
        f"Админ: {ADMIN_ID}\n"
        f"Интервал автопоста: каждые {interval} ч\n"
        f"max_tokens (размер текста): {tokens}\n"
        f"OpenRouter key: {masked}\n"
        f"Model: {context.bot_data.get('model', OPENROUTER_MODEL)}\n"
        f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


async def cmd_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Импорт / смена OpenRouter API-ключа на лету."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ.")
        return

    if not context.args:
        await update.message.reply_text(
            "Использование:\n"
            "/key sk-or-v1-твой_ключ\n\n"
            "Ключ OpenRouter. Сразу применится, перезапуск не нужен."
        )
        return

    new_key = context.args[0].strip()
    if len(new_key) < 20:
        await update.message.reply_text("Ключ слишком короткий. Проверь.")
        return
    if not new_key.startswith("sk-or-"):
        await update.message.reply_text("Ожидается ключ OpenRouter: sk-or-v1-...")
        return

    try:
        new_client = setup_client(new_key)
        context.bot_data["client"] = new_client
        context.bot_data["api_key"] = new_key
        _persist_env("OPENROUTER_API_KEY", new_key)

        masked = new_key[:6] + "…" + new_key[-4:] if len(new_key) > 12 else "***"
        await update.message.reply_text(
            f"Ключ принят и применён.\n"
            f"Маска: `{masked}`\n"
            f"Клиент пересоздан. Можно сразу жать /post."
        )
        logger.info("OpenRouter API key updated via /key")
    except Exception as e:
        logger.error(f"/key error: {e}")
        await update.message.reply_text(f"Ошибка при установке ключа:\n{e}")


async def cmd_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "Рекомендуемые модели OpenRouter:\n\n"
        "Бесплатные:\n"
        "• deepseek/deepseek-chat-v3-0324:free\n"
        "• meta-llama/llama-3.3-70b-instruct:free\n"
        "• google/gemma-3-27b-it:free\n"
        "• qwen/qwen3-235b-a22b:free\n\n"
        "Платные (лучше по качеству):\n"
        "• google/gemini-2.5-flash\n"
        "• google/gemini-2.5-pro\n"
        "• anthropic/claude-sonnet-4\n"
        "• deepseek/deepseek-chat\n\n"
        "Сменить:\n"
        "/model deepseek/deepseek-chat-v3-0324:free\n"
        "/model google/gemini-2.5-flash"
    )


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Смена модели OpenRouter на лету."""
    global CURRENT_MODEL
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ.")
        return

    if not context.args:
        await update.message.reply_text(
            f"Текущая модель:\n`{CURRENT_MODEL}`\n\n"
            "Сменить: /model provider/model-name\n"
            "Список: /models"
        )
        return

    new_model = context.args[0].strip()
    if len(new_model) < 3:
        await update.message.reply_text("Слишком короткое имя модели.")
        return

    CURRENT_MODEL = new_model
    context.bot_data["model"] = new_model
    _persist_env("OPENROUTER_MODEL", new_model)

    await update.message.reply_text(f"Модель сменена на:\n`{new_model}`")
    logger.info(f"Model changed to {new_model}")


async def cmd_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Редактируемый размер текста (max_tokens)."""
    global MAX_TOKENS
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ.")
        return

    if not context.args:
        await update.message.reply_text(
            f"Текущий max_tokens: `{MAX_TOKENS}`\n\n"
            "Сменить: /tokens 512\n"
            "Рекомендуется 256–1024. 512 — баланс длины и стоимости."
        )
        return

    try:
        new_val = int(context.args[0].strip())
    except ValueError:
        await update.message.reply_text("Нужно целое число, например /tokens 512")
        return

    if new_val < 64 or new_val > 4096:
        await update.message.reply_text("Диапазон: 64–4096.")
        return

    MAX_TOKENS = new_val
    context.bot_data["max_tokens"] = new_val
    _persist_env("MAX_TOKENS", str(new_val))

    await update.message.reply_text(f"max_tokens = `{new_val}`\nДлина ответа модели обновлена.")
    logger.info(f"MAX_TOKENS set to {new_val}")


async def cmd_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Редактируемое время автопостинга (в часах)."""
    global POST_INTERVAL_HOURS
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ.")
        return

    if not context.args:
        await update.message.reply_text(
            f"Текущий интервал: `{POST_INTERVAL_HOURS}` ч\n\n"
            "Сменить: /interval 1\n"
            "Можно дробный: /interval 0.5  → каждые 30 минут\n"
            "Минимум ~0.0167 ч (1 минута)."
        )
        return

    try:
        new_val = float(context.args[0].strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("Нужно число, например /interval 1 или /interval 0.5")
        return

    if new_val < 0.0167 or new_val > 168:
        await update.message.reply_text("Диапазон: от ~1 минуты до 168 часов (неделя).")
        return

    POST_INTERVAL_HOURS = new_val
    context.bot_data["interval_hours"] = new_val
    _persist_env("POST_INTERVAL_HOURS", str(new_val))

    _reschedule_job(context.application, new_val)

    await update.message.reply_text(
        f"Интервал автопостинга = `{new_val}` ч\n"
        f"(примерно каждые {int(new_val * 60)} мин)\n"
        "Job пересоздан."
    )
    logger.info(f"POST_INTERVAL_HOURS set to {new_val}")


async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ.")
        return

    topic = " ".join(context.args) if context.args else None
    await update.message.reply_text("Генерирую обращение Рализма…")

    client = context.bot_data.get("client") or setup_client()
    text = await generate_appeal(client, topic)

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
    """Автопостинг по интервалу."""
    client = context.bot_data.get("client") or setup_client()
    text = await generate_appeal(client)
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text)
        logger.info("Scheduled post sent")
    except Exception as e:
        logger.error(f"Scheduled post failed: {e}")


async def post_init(app: Application):
    app.bot_data["api_key"] = OPENROUTER_API_KEY
    app.bot_data["client"] = setup_client()
    app.bot_data["model"] = CURRENT_MODEL
    app.bot_data["max_tokens"] = MAX_TOKENS
    app.bot_data["interval_hours"] = POST_INTERVAL_HOURS
    logger.info(
        f"OpenRouter ready, model={OPENROUTER_MODEL}, "
        f"max_tokens={MAX_TOKENS}, interval={POST_INTERVAL_HOURS}h"
    )


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
    app.add_handler(CommandHandler("key", cmd_key))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(CommandHandler("tokens", cmd_tokens))
    app.add_handler(CommandHandler("interval", cmd_interval))
    app.add_handler(CommandHandler("post", cmd_post))

    job_queue = app.job_queue
    if job_queue:
        interval_sec = max(60.0, POST_INTERVAL_HOURS * 3600.0)
        job_queue.run_repeating(
            hourly_post,
            interval=interval_sec,
            first=60,
            name="hourly_post",
        )
        logger.info(f"Job scheduled every {POST_INTERVAL_HOURS}h ({interval_sec}s)")

    logger.info("Bot starting…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
