#!/usr/bin/env python3
"""
Бот обращений Тимофея Рализма (2060–2062)
Канал: -1004341947372
Админ: 7787565361

Редактируемые:
  POST_INTERVAL_HOURS — интервал автопостинга
  MAX_TOKENS          — размер ответа

Контекст:
  Любой текст и файлы (.txt .md .docx .pdf .json .csv и др.) от админа
  → /context_add, документы, /context, /context_clear, /context_export
"""

import os
import re
import logging
import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

# ================== КОНФИГ ==================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ВСТАВЬ_ТОКЕН_БОТА")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1004341947372"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "7787565361"))

POST_INTERVAL_HOURS = float(os.getenv("POST_INTERVAL_HOURS", "1"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "512"))

OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3-0324:free")
CURRENT_MODEL = OPENROUTER_MODEL

# Папка для загруженного контекста
CONTEXT_DIR = Path(os.getenv("CONTEXT_DIR", "ralizm_context"))
CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
# Лимит суммарного доп. контекста (символы)
MAX_EXTRA_CONTEXT_CHARS = int(os.getenv("MAX_EXTRA_CONTEXT_CHARS", "80000"))

# ================== ЛОГИ ==================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("ralizm_bot")

# ================== БАЗОВЫЙ СИСТЕМНЫЙ ПРОМПТ ==================
BASE_SYSTEM_PROMPT = """
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

# ================== КОНТЕКСТ: ЗАГРУЗКА / ХРАНЕНИЕ ==================

def _safe_filename(name: str) -> str:
    name = re.sub(r"[^\w.\-а-яА-ЯёЁ ]+", "_", name, flags=re.UNICODE)
    name = name.strip().replace(" ", "_")[:120]
    return name or f"file_{int(datetime.now().timestamp())}"


def list_context_files() -> list:
    if not CONTEXT_DIR.exists():
        return []
    return sorted(
        [p for p in CONTEXT_DIR.iterdir() if p.is_file() and not p.name.startswith(".")],
        key=lambda p: p.stat().st_mtime,
    )


def load_extra_context(max_chars: int = None) -> str:
    if max_chars is None:
        max_chars = MAX_EXTRA_CONTEXT_CHARS
    files = list_context_files()
    chunks = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                continue
            header = f"\n--- [контекст: {p.name}] ---\n"
            chunks.append((header, text))
        except Exception as e:
            logger.warning(f"Не прочитать {p}: {e}")
    assembled = []
    used = 0
    for header, text in reversed(chunks):
        piece = header + text
        if used + len(piece) > max_chars:
            remain = max_chars - used - len(header)
            if remain > 200:
                assembled.append(header + text[-remain:])
                used += len(header) + remain
            break
        assembled.append(piece)
        used += len(piece)
    assembled.reverse()
    return "\n".join(assembled).strip()


def build_system_prompt() -> str:
    extra = load_extra_context()
    if not extra:
        return BASE_SYSTEM_PROMPT
    return (
        BASE_SYSTEM_PROMPT
        + "\n\n### Дополнительный загруженный контекст (используй как факты лора, имена, события)\n"
        + extra
    )


def save_text_as_context(text: str, source_name: str = "manual") -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = _safe_filename(source_name)
    path = CONTEXT_DIR / f"{ts}_{safe}.txt"
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def context_stats():
    files = list_context_files()
    total_chars = 0
    names = []
    for p in files:
        try:
            n = p.stat().st_size
            total_chars += n
            names.append(f"{p.name} (~{n} б)")
        except Exception:
            names.append(p.name)
    return len(files), total_chars, names


def extract_text_from_bytes(data: bytes, filename: str) -> str:
    name = filename.lower()
    if name.endswith((".txt", ".md", ".markdown", ".json", ".csv", ".log", ".yml", ".yaml", ".xml", ".html", ".htm", ".rst")):
        for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    if name.endswith(".docx"):
        try:
            import zipfile
            import io
            from xml.etree import ElementTree as ET
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read("word/document.xml")
            root = ET.fromstring(xml)
            paras = []
            for p in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                texts = [t.text for t in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t") if t.text]
                if texts:
                    paras.append("".join(texts))
            return "\n".join(paras)
        except Exception as e:
            logger.warning(f"docx extract fail: {e}")
            return f"[не удалось разобрать docx: {e}]"

    if name.endswith(".pdf"):
        try:
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            pages = []
            for page in reader.pages:
                t = page.extract_text() or ""
                if t.strip():
                    pages.append(t)
            return "\n\n".join(pages) if pages else "[pdf без текста / скан]"
        except ImportError:
            return (
                "[PDF: установи pypdf: pip install pypdf — или пришли .txt/.md/.docx]\n"
                f"Файл: {filename}, размер {len(data)} байт"
            )
        except Exception as e:
            return f"[ошибка чтения pdf: {e}]"

    try:
        text = data.decode("utf-8")
        if sum(1 for c in text[:2000] if c.isprintable() or c.isspace()) / max(len(text[:2000]), 1) > 0.85:
            return text
    except Exception:
        pass
    return f"[бинарный/неизвестный формат: {filename}, {len(data)} байт — пришли .txt .md .docx .pdf]"


# ================== OPENROUTER ==================
import json
import urllib.request
import urllib.error


def setup_client(api_key=None):
    return api_key or OPENROUTER_API_KEY


def _get_max_tokens() -> int:
    return MAX_TOKENS


def _call_openrouter(api_key: str, user_prompt: str) -> str:
    if not api_key or not api_key.startswith("sk-or-"):
        raise RuntimeError(
            "Нужен ключ OpenRouter вида sk-or-v1-...\n"
            "Поставь через /key sk-or-v1-..."
        )

    system = build_system_prompt()
    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": CURRENT_MODEL,
        "messages": [
            {"role": "system", "content": system},
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
        with urllib.request.urlopen(req, timeout=120) as resp:
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


async def generate_appeal(client, topic=None) -> str:
    api_key = client if isinstance(client, str) else (client or OPENROUTER_API_KEY)

    if topic:
        user_prompt = (
            f"Напиши новое обращение Тимофея Рализма на тему: {topic}. "
            "Стиль жёсткий, с матами, абсурдом и договорным цинизмом. "
            "Используй весь доступный контекст (базовый + загруженный). "
            "Только текст обращения."
        )
    else:
        user_prompt = (
            "Напиши новое обращение Тимофея Рализма к гражданам САР Кефирстан "
            "в 2060–2062 году. Тема любая актуальная: остатки войны, Фонд, долги, "
            "ЧВК, Молозорь, Твороград, март 2059, СВД, Турбания, Дервесния, "
            "«добровольный выбор». Используй весь загруженный контекст. "
            "Стиль — маты, абсурд, холодный цинизм. Только текст обращения."
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
    job_queue = app.job_queue
    if not job_queue:
        return
    for job in list(job_queue.jobs()):
        if job.name == "hourly_post" or (
            job.callback and getattr(job.callback, "__name__", "") == "hourly_post"
        ):
            job.schedule_removal()
    interval_sec = max(60.0, hours * 3600.0)
    job_queue.run_repeating(
        hourly_post,
        interval=interval_sec,
        first=60,
        name="hourly_post",
    )
    logger.info(f"Job rescheduled: every {hours}h ({interval_sec}s)")


def _is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)


# ================== HANDLERS ==================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    n, chars, _ = context_stats()
    await update.message.reply_text(
        "Бот Рализма 2060–2062 онлайн.\n\n"
        "Команды:\n"
        "/key <ключ> — OpenRouter API-ключ\n"
        "/model <id> — модель\n"
        "/models — список моделей\n"
        "/tokens <N> — max_tokens (по умолч. 512)\n"
        "/interval <часы> — автопост (0.5 = 30 мин)\n"
        "/post [тема] — сгенерировать в канал\n"
        "/status — статус\n\n"
        "Контекст (любые файлы и текст):\n"
        "/context — что загружено\n"
        "/context_add <текст> — добавить текст\n"
        "/context_clear — очистить всё\n"
        "/context_export — выгрузить сводку\n"
        "Просто пришли файл (.txt .md .docx .pdf …) — добавится в контекст\n\n"
        f"Сейчас: tokens={MAX_TOKENS}, interval={POST_INTERVAL_HOURS}ч, "
        f"контекст-файлов={n} (~{chars} б)"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    key = context.bot_data.get("api_key") or OPENROUTER_API_KEY
    masked = (key[:6] + "…" + key[-4:]) if key and len(key) > 12 else "не задан"
    interval = context.bot_data.get("interval_hours", POST_INTERVAL_HOURS)
    tokens = context.bot_data.get("max_tokens", MAX_TOKENS)
    n, chars, names = context_stats()
    files_preview = "\n".join(f"  • {x}" for x in names[-8:]) if names else "  (пусто)"
    await update.message.reply_text(
        f"Канал: {CHANNEL_ID}\n"
        f"Админ: {ADMIN_ID}\n"
        f"Интервал: каждые {interval} ч\n"
        f"max_tokens: {tokens}\n"
        f"OpenRouter key: {masked}\n"
        f"Model: {context.bot_data.get('model', OPENROUTER_MODEL)}\n"
        f"Контекст: {n} файл(ов), ~{chars} байт\n"
        f"{files_preview}\n"
        f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


async def cmd_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await update.message.reply_text("Только админ.")
        return
    if not context.args:
        await update.message.reply_text("Использование:\n/key sk-or-v1-твой_ключ")
        return
    new_key = context.args[0].strip()
    if len(new_key) < 20 or not new_key.startswith("sk-or-"):
        await update.message.reply_text("Ожидается ключ OpenRouter: sk-or-v1-...")
        return
    try:
        context.bot_data["client"] = setup_client(new_key)
        context.bot_data["api_key"] = new_key
        _persist_env("OPENROUTER_API_KEY", new_key)
        masked = new_key[:6] + "…" + new_key[-4:]
        await update.message.reply_text(f"Ключ принят.\nМаска: `{masked}`")
        logger.info("API key updated")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def cmd_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    await update.message.reply_text(
        "Бесплатные:\n"
        "• deepseek/deepseek-chat-v3-0324:free\n"
        "• meta-llama/llama-3.3-70b-instruct:free\n"
        "• google/gemma-3-27b-it:free\n"
        "• qwen/qwen3-235b-a22b:free\n\n"
        "Платные:\n"
        "• google/gemini-2.5-flash\n"
        "• google/gemini-2.5-pro\n"
        "• anthropic/claude-sonnet-4\n"
        "• deepseek/deepseek-chat\n\n"
        "/model provider/model-name"
    )


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CURRENT_MODEL
    if not _is_admin(update):
        await update.message.reply_text("Только админ.")
        return
    if not context.args:
        await update.message.reply_text(f"Текущая: `{CURRENT_MODEL}`\n/model id")
        return
    new_model = context.args[0].strip()
    if len(new_model) < 3:
        await update.message.reply_text("Слишком короткое имя.")
        return
    CURRENT_MODEL = new_model
    context.bot_data["model"] = new_model
    _persist_env("OPENROUTER_MODEL", new_model)
    await update.message.reply_text(f"Модель: `{new_model}`")


async def cmd_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MAX_TOKENS
    if not _is_admin(update):
        await update.message.reply_text("Только админ.")
        return
    if not context.args:
        await update.message.reply_text(f"Сейчас max_tokens=`{MAX_TOKENS}`\n/tokens 512")
        return
    try:
        new_val = int(context.args[0].strip())
    except ValueError:
        await update.message.reply_text("Нужно число: /tokens 512")
        return
    if new_val < 64 or new_val > 4096:
        await update.message.reply_text("Диапазон 64–4096")
        return
    MAX_TOKENS = new_val
    context.bot_data["max_tokens"] = new_val
    _persist_env("MAX_TOKENS", str(new_val))
    await update.message.reply_text(f"max_tokens = `{new_val}`")


async def cmd_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global POST_INTERVAL_HOURS
    if not _is_admin(update):
        await update.message.reply_text("Только админ.")
        return
    if not context.args:
        await update.message.reply_text(
            f"Сейчас `{POST_INTERVAL_HOURS}` ч\n"
            "/interval 1   или   /interval 0.5"
        )
        return
    try:
        new_val = float(context.args[0].strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("Нужно число")
        return
    if new_val < 0.0167 or new_val > 168:
        await update.message.reply_text("От ~1 мин до 168 ч")
        return
    POST_INTERVAL_HOURS = new_val
    context.bot_data["interval_hours"] = new_val
    _persist_env("POST_INTERVAL_HOURS", str(new_val))
    _reschedule_job(context.application, new_val)
    await update.message.reply_text(
        f"Интервал = `{new_val}` ч (~{int(new_val * 60)} мин). Job обновлён."
    )


async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await update.message.reply_text("Только админ.")
        return
    topic = " ".join(context.args) if context.args else None
    await update.message.reply_text("Генерирую обращение Рализма…")
    client = context.bot_data.get("client") or setup_client()
    text = await generate_appeal(client, topic)
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=None)
        await update.message.reply_text("Отправлено в канал.")
    except Exception as e:
        logger.error(f"Send error: {e}")
        await update.message.reply_text(f"Ошибка отправки: {e}\n\n{text}")


async def cmd_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    n, chars, names = context_stats()
    if n == 0:
        await update.message.reply_text(
            "Контекст пуст.\n"
            "Пришли файл (.txt .md .docx .pdf …) или:\n"
            "/context_add любой текст лора\n"
            f"Лимит суммарно ~{MAX_EXTRA_CONTEXT_CHARS} символов."
        )
        return
    preview = "\n".join(f"• {x}" for x in names)
    extra_len = len(load_extra_context())
    await update.message.reply_text(
        f"Файлов: {n}\n"
        f"На диске: ~{chars} байт\n"
        f"В промпте сейчас: ~{extra_len} символов (лимит {MAX_EXTRA_CONTEXT_CHARS})\n\n"
        f"{preview}\n\n"
        "/context_clear — очистить\n"
        "/context_export — сводка\n"
        "Новый файл/текст — просто пришли."
    )


async def cmd_context_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await update.message.reply_text("Только админ.")
        return
    text = " ".join(context.args).strip() if context.args else ""
    if not text and update.message.reply_to_message:
        r = update.message.reply_to_message
        text = (r.text or r.caption or "").strip()
    if not text:
        await update.message.reply_text(
            "Использование:\n"
            "/context_add текст лора…\n"
            "или ответь /context_add на любое сообщение с текстом\n"
            "или просто пришли документ."
        )
        return
    if len(text) < 5:
        await update.message.reply_text("Слишком коротко.")
        return
    path = save_text_as_context(text, source_name="manual_add")
    n, chars, _ = context_stats()
    await update.message.reply_text(
        f"Добавлено в контекст: `{path.name}`\n"
        f"Символов в куске: {len(text)}\n"
        f"Всего файлов: {n} (~{chars} б)"
    )


async def cmd_context_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await update.message.reply_text("Только админ.")
        return
    files = list_context_files()
    removed = 0
    for p in files:
        try:
            p.unlink()
            removed += 1
        except Exception as e:
            logger.warning(f"Не удалить {p}: {e}")
    await update.message.reply_text(f"Очищено. Удалено файлов: {removed}.")


async def cmd_context_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    extra = load_extra_context()
    if not extra:
        await update.message.reply_text("Контекст пуст.")
        return
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix="_context_export.txt", delete=False
    ) as f:
        f.write(extra)
        tmp = f.name
    try:
        with open(tmp, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename="ralizm_context_export.txt",
                caption=f"Сводка контекста, ~{len(extra)} символов",
            )
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    doc = update.message.document
    if not doc:
        return
    filename = doc.file_name or f"doc_{doc.file_unique_id}"
    if doc.file_size and doc.file_size > 15 * 1024 * 1024:
        await update.message.reply_text("Файл больше 15 МБ — не беру.")
        return

    await update.message.reply_text(f"Читаю `{filename}`…")
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        data = bytes(await tg_file.download_as_bytearray())
        text = await asyncio.to_thread(extract_text_from_bytes, data, filename)
        text = text.strip()
        if not text or text.startswith("[бинарный") or text.startswith("[PDF: установи"):
            await update.message.reply_text(
                f"Не удалось извлечь полезный текст из `{filename}`.\n{text[:500]}"
            )
            return
        if len(text) > MAX_EXTRA_CONTEXT_CHARS:
            text = text[:MAX_EXTRA_CONTEXT_CHARS]
            note = " (обрезано до лимита)"
        else:
            note = ""
        path = save_text_as_context(text, source_name=filename)
        n, chars, _ = context_stats()
        preview = text[:280].replace("\n", " ")
        await update.message.reply_text(
            f"Контекст обновлён{note}.\n"
            f"Файл: `{path.name}`\n"
            f"Извлечено: {len(text)} символов\n"
            f"Всего в хранилище: {n} файл(ов), ~{chars} б\n\n"
            f"Превью: {preview}…"
        )
    except Exception as e:
        logger.exception("document ingest failed")
        await update.message.reply_text(f"Ошибка при разборе файла: {e}")


async def on_text_as_context_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("/"):
        return
    if len(text) >= 200:
        await update.message.reply_text(
            "Похоже на лор-текст. Чтобы добавить в контекст бота, ответь на это сообщение командой:\n"
            "/context_add\n"
            "или пришли как файл .txt / .md"
        )


async def hourly_post(context: ContextTypes.DEFAULT_TYPE):
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
    n, chars, _ = context_stats()
    logger.info(
        f"OpenRouter ready model={OPENROUTER_MODEL} tokens={MAX_TOKENS} "
        f"interval={POST_INTERVAL_HOURS}h context_files={n} (~{chars}b)"
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

    app.add_handler(CommandHandler("context", cmd_context))
    app.add_handler(CommandHandler("context_add", cmd_context_add))
    app.add_handler(CommandHandler("context_clear", cmd_context_clear))
    app.add_handler(CommandHandler("context_export", cmd_context_export))

    app.add_handler(
        MessageHandler(filters.Document.ALL & filters.User(ADMIN_ID), on_document)
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_ID), on_text_as_context_hint)
    )

    job_queue = app.job_queue
    if job_queue:
        interval_sec = max(60.0, POST_INTERVAL_HOURS * 3600.0)
        job_queue.run_repeating(
            hourly_post,
            interval=interval_sec,
            first=60,
            name="hourly_post",
        )
        logger.info(f"Job every {POST_INTERVAL_HOURS}h ({interval_sec}s)")

    logger.info("Bot starting…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
