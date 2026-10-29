import io
import json
import random
import time
from dataclasses import replace

from aiogram import BaseMiddleware, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update

from .provider import ModelError


async def resolve_chats(bot, config):
    """Names bootstrap once. All update authorization uses verified numeric IDs."""
    channel = await bot.get_chat(config.channel_id or config.channel_username)
    discussion = await bot.get_chat(config.discussion_id or config.discussion_username)
    if channel.type != "channel" or discussion.type != "supergroup":
        raise ValueError("Expected channel + supergroup")
    if channel.linked_chat_id != discussion.id or discussion.linked_chat_id != channel.id:
        raise ValueError("Channel/discussion are not linked")
    if config.channel_id and channel.id != config.channel_id:
        raise ValueError("Channel ID mismatch")
    if config.discussion_id and discussion.id != config.discussion_id:
        raise ValueError("Discussion ID mismatch")
    member = await bot.get_chat_member(channel.id, bot.id)
    if member.status not in ("administrator", "creator") or (
        member.status == "administrator" and not member.can_post_messages
    ):
        raise ValueError("Bot lacks channel posting rights")
    group_member = await bot.get_chat_member(discussion.id, bot.id)
    if group_member.status not in ("administrator", "creator"):
        raise ValueError("Bot must be discussion administrator to receive comments")
    return replace(config, channel_id=channel.id, discussion_id=discussion.id)


def admin_allowed(user_id, chat_id, chat_type, config, is_bot=False):
    return (
        not is_bot
        and user_id == config.owner_id
        and chat_id == config.owner_id
        and chat_type == "private"
    )


class Access(BaseMiddleware):
    def __init__(self, config, store):
        self.config, self.store = config, store

    async def __call__(self, handler, event: Update, data):
        callback = event.callback_query
        msg = event.message or event.channel_post
        if callback:
            m = callback.message
            if not m or not admin_allowed(
                callback.from_user.id,
                m.chat.id,
                m.chat.type,
                self.config,
                callback.from_user.is_bot,
            ):
                return
        elif msg:
            if msg.chat.type == "private":
                if not msg.from_user or not admin_allowed(
                    msg.from_user.id, msg.chat.id, msg.chat.type, self.config, msg.from_user.is_bot
                ):
                    return
            elif msg.chat.id not in (self.config.channel_id, self.config.discussion_id):
                return
            elif msg.chat.id == self.config.channel_id:
                return  # No inbound channel commands or model calls.
            elif msg.is_automatic_forward:
                if not msg.sender_chat or msg.sender_chat.id != self.config.channel_id:
                    return
            elif not msg.from_user or msg.from_user.is_bot or msg.sender_chat:
                return
        else:
            return
        if not self.store.once("update:" + str(event.update_id)):
            return
        return await handler(event, data)


HELP = """Панель Тимофея
/news - новая новость
/delirium - Белая горячка
/drafts - черновики
/preview ID - предпросмотр
/edit ID новый текст - редактировать с повторной проверкой канона
/publish ID - публикация
/autopost on|off - расписание
/mode manual|automatic - режим (по умолчанию manual)
/interval МИНУТЫ - интервал от 10 до 10080
/probability 0..1 - шанс ответа
/memory ID - память гражданина
/forget ID - удалить память гражданина
/lore ЗАГОЛОВОК | ТЕКСТ - добавить подтвержденный лор
Или отправь UTF-8 .txt/.md до 100 КБ в личку
/usage - расход токенов и резервы
/key НОВЫЙ_КЛЮЧ - сменить ключ в окружении текущего процесса
/model ID_МОДЕЛИ - проверить и сменить модель xKiro
/errors - журнал ошибок
/uncertain - спорная доставка
/reconcile ID MESSAGE_ID - отметить публикацию после проверки канала
/pause - отключить генерацию, ответы, публикации
/resume - возобновить
/stop - полностью остановить процесс
/status - настройки и ID
"""


def panel():
    rows = [
        [("Новость", "news"), ("Белая горячка", "delirium")],
        [("Черновики", "drafts"), ("Статус", "status")],
        [("Автопост вкл", "autopost on"), ("Автопост выкл", "autopost off")],
        [("Ручной режим", "mode manual"), ("Автоматический", "mode automatic")],
        [("Токены", "usage"), ("Ошибки", "errors")],
        [("Пауза", "pause"), ("Продолжить", "resume")],
        [("Команды", "help"), ("Остановить процесс", "stop")],
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label, callback_data="cmd:" + command)
                for label, command in row
            ]
            for row in rows
        ]
    )


def build_router(service):
    router = Router()
    config, store = service.config, service.store

    async def command(message, raw):
        if not message.from_user or not admin_allowed(
            message.from_user.id,
            message.chat.id,
            message.chat.type,
            config,
            message.from_user.is_bot,
        ):
            return
        name, _, args = raw.strip().partition(" ")
        name = name.lstrip("/").split("@")[0].lower()
        try:
            if name == "key":
                if not args:
                    await message.answer(
                        "Ключ установлен" if config.api_key else "Ключ не установлен"
                    )
                    return
                deleted = True
                try:
                    await message.delete()
                except Exception:
                    deleted = False
                await service.desk.model.change_key(args.strip())
                text = "Ключ заменен в окружении процесса. Значение скрыто. После перезапуска используется XKIRO_API_KEY из окружения сервера"
                if not deleted:
                    text += "\nСообщение с ключом удалить не удалось - удали его вручную"
                await message.answer(text)
            elif name == "model":
                if not args:
                    await message.answer("Модель: " + config.model)
                else:
                    tier = await service.desk.model.change_model(args.strip())
                    text = "Модель найдена и сохранена: " + config.model
                    if tier != "free":
                        text += "\nКаталог помечает ее платной. Генерация заблокирована, чтобы не списывать деньги"
                    await message.answer(text)
            elif name in ("start", "admin", "help"):
                await message.answer(HELP, reply_markup=panel())
            elif name in ("news", "delirium"):
                await message.answer("Генерация и проверка канона...")
                event_id = await service.generate("delirium" if name == "delirium" else None)
                await service.preview(event_id)
            elif name == "drafts":
                rows = store.conn.execute(
                    "SELECT id,kind,location FROM events WHERE state='draft' ORDER BY id DESC LIMIT 20"
                ).fetchall()
                await message.answer(
                    "\n".join(f"#{r[0]} {r[1]} - {r[2]}" for r in rows) or "Черновиков нет"
                )
            elif name == "preview":
                await service.preview(int(args))
            elif name == "edit":
                event_id, _, text = args.partition(" ")
                await service.edit(int(event_id), text)
                await service.preview(int(event_id))
            elif name == "publish":
                mid = await service.publish(int(args))
                await message.answer(f"Опубликовано: сообщение {mid}")
            elif name == "autopost" and args in ("on", "off"):
                store.set("autopost", args == "on")
                store.set(
                    "next_post", time.time() + store.get("interval", config.interval_minutes) * 60
                )
                await message.answer("Автопост: " + args)
            elif name == "mode" and args in ("manual", "automatic"):
                store.set("mode", args)
                await message.answer("Режим: " + args)
            elif name == "interval" and 10 <= int(args) <= 10080:
                store.set("interval", int(args))
                store.set("next_post", time.time() + int(args) * 60)
                await message.answer("Интервал сохранен")
            elif name == "probability" and 0 <= float(args) <= 1:
                store.set("probability", float(args))
                await message.answer("Вероятность сохранена")
            elif name == "memory":
                await message.answer(
                    json.dumps(store.history(int(args)), ensure_ascii=False)[:3900] or "[]"
                )
            elif name == "forget":
                store.conn.execute("DELETE FROM memory WHERE user_id=?", (int(args),))
                await message.answer("Память гражданина удалена")
            elif name == "lore":
                title, sep, text = args.partition("|")
                if not sep or not title.strip() or not text.strip():
                    raise ValueError()
                service.desk.kb.add(title.strip(), text.strip())
                await message.answer("Подтвержденный лор добавлен в поиск")
            elif name == "usage":
                row = store.conn.execute(
                    "SELECT count(*),coalesce(sum(prompt),0),coalesce(sum(completion),0),coalesce(sum(total),0),coalesce(sum(CASE WHEN total IS NULL THEN reserved ELSE 0 END),0) FROM calls"
                ).fetchone()
                await message.answer(
                    f"Вызовы: {row[0]}\nВход: {row[1]}\nВыход: {row[2]}\nВсего по API: {row[3]}\nНеуточненные резервы: {row[4]}\nПлатные маршруты: запрещены"
                )
            elif name == "errors":
                rows = store.conn.execute(
                    "SELECT component,code FROM errors ORDER BY id DESC LIMIT 15"
                ).fetchall()
                await message.answer("\n".join(f"{r[0]}: {r[1]}" for r in rows) or "Ошибок нет")
            elif name == "uncertain":
                rows = store.conn.execute(
                    "SELECT id,body FROM events WHERE state='uncertain' LIMIT 10"
                ).fetchall()
                await message.answer(
                    "\n".join(f"#{r[0]} {r[1][:150]}" for r in rows) or "Спорных доставок нет"
                )
            elif name == "reconcile":
                eid, mid = map(int, args.split())
                row = store.event(eid)
                if not row or row["state"] != "uncertain" or mid <= 0:
                    raise ValueError()
                store.published(eid, mid)
                await message.answer("Доставка отмечена по подтверждению владельца")
            elif name in ("pause", "resume"):
                store.set("paused", name == "pause")
                await message.answer(
                    "Работа приостановлена" if name == "pause" else "Работа возобновлена"
                )
            elif name == "stop":
                store.set("paused", True)
                store.set("autopost", False)
                await message.answer(
                    "Процесс останавливается. Для запуска потребуется снова запустить Python"
                )
                service.stop_event.set()
            elif name == "status":
                await message.answer(
                    f"Канал: {config.channel_id}\nЧат: {config.discussion_id}\nВладелец: {config.owner_id}\nПауза: {not service.enabled()}\nАвтопост: {store.get('autopost', False)}\nРежим: {store.get('mode', 'manual')}\nИнтервал: {store.get('interval', config.interval_minutes)} мин\nШанс ответа: {store.get('probability', config.reply_probability)}\nМодель: {config.model}\nГенератор новостей: plain-v2\nПроверка комментариев: canon-v2",
                    reply_markup=panel(),
                )
            else:
                await message.answer("Неверная команда или аргументы. /help")
        except (ValueError, TypeError):
            await message.answer("Неверные аргументы. /help")
        except Exception as exc:
            code = str(exc) if isinstance(exc, ModelError) else "OPERATION_FAILED"
            store.error("admin", code)
            await message.answer("Операция не выполнена: " + code)

    @router.callback_query()
    async def callback(event: CallbackQuery):
        if not event.message or not admin_allowed(
            event.from_user.id,
            event.message.chat.id,
            event.message.chat.type,
            config,
            event.from_user.is_bot,
        ):
            return
        await event.answer()
        action, _, arg = (event.data or "").partition(":")
        # Callback message belongs to bot. Use explicit verified callback actor.
        owner_message = event.message.model_copy(update={"from_user": event.from_user})
        if action == "cmd":
            await command(owner_message, arg)
        elif action == "publish":
            await command(owner_message, "publish " + arg)
        elif action == "discard":
            try:
                store.conn.execute(
                    "UPDATE events SET state='discarded' WHERE id=? AND state='draft'", (int(arg),)
                )
                await owner_message.answer("Черновик удален")
            except ValueError:
                pass

    @router.message()
    async def message(msg: Message):
        if msg.chat.id == config.owner_id and msg.chat.type == "private":
            if msg.document:
                doc = msg.document
                if (doc.file_size or 100001) > 100000 or not (doc.file_name or "").lower().endswith(
                    (".txt", ".md")
                ):
                    await msg.answer("Нужен UTF-8 .txt/.md до 100 КБ")
                    return
                try:
                    stream = io.BytesIO()
                    await service.bot.download(doc, destination=stream)
                    body = stream.getvalue().decode("utf-8-sig")
                    service.desk.kb.add(doc.file_name, body)
                    await msg.answer("Лор добавлен в поиск")
                except Exception:
                    store.error("lore_upload", "INVALID_OR_UNAVAILABLE_DOCUMENT")
                    await msg.answer("Не удалось прочитать документ")
            elif msg.text:
                await command(msg, msg.text)
            return
        if msg.chat.id != config.discussion_id:
            return
        text = (msg.text or msg.caption or "")[:2000]
        if msg.is_automatic_forward and msg.sender_chat and msg.sender_chat.id == config.channel_id:
            origin = msg.forward_origin
            channel_mid = getattr(origin, "message_id", 0)
            store.conn.execute(
                "INSERT OR REPLACE INTO roots VALUES (?,?,?)", (msg.message_id, channel_mid, text)
            )
            return
        if (
            not service.enabled()
            or not text
            or text.startswith("/")
            or not msg.from_user
            or msg.from_user.is_bot
            or msg.sender_chat
        ):
            return
        parent = msg.reply_to_message
        if not parent:
            return
        root = store.conn.execute(
            "SELECT message_id,body FROM roots WHERE message_id=?", (parent.message_id,)
        ).fetchone()
        if not root:
            mapped = store.conn.execute(
                "SELECT root_id FROM messages WHERE message_id=?", (parent.message_id,)
            ).fetchone()
            if mapped:
                root = store.conn.execute(
                    "SELECT message_id,body FROM roots WHERE message_id=?", (mapped[0],)
                ).fetchone()
        # Root can arrive nested in the first comment after a restart/adding bot.
        if (
            not root
            and parent.is_automatic_forward
            and parent.sender_chat
            and parent.sender_chat.id == config.channel_id
        ):
            store.conn.execute(
                "INSERT OR IGNORE INTO roots VALUES (?,?,?)",
                (
                    parent.message_id,
                    getattr(parent.forward_origin, "message_id", 0),
                    parent.text or parent.caption or "",
                ),
            )
            root = store.conn.execute(
                "SELECT message_id,body FROM roots WHERE message_id=?", (parent.message_id,)
            ).fetchone()
        if not root:
            return
        user = msg.from_user.id
        store.remember(user, "user", text, config.memory_messages, config.memory_days)
        store.conn.execute(
            "INSERT OR IGNORE INTO messages VALUES (?,?,?,?)",
            (msg.message_id, root[0], text, time.time()),
        )
        if store.cooling(f"user:{user}") or store.cooling("reply:global") or service.work.locked():
            return
        if random.random() >= store.get("probability", config.reply_probability):
            return
        store.cool(f"user:{user}", config.user_cooldown)
        store.cool("reply:global", config.global_reply_cooldown)
        try:
            async with service.work:
                answer = await service.desk.reply(
                    user, text, root[1] + "\nПредыдущая реплика: " + (parent.text or "")[:1500]
                )
                # Recheck pause after long generation.
                if not service.enabled():
                    return
                sent = await msg.reply(answer, parse_mode=None)
                store.remember(
                    user, "assistant", answer, config.memory_messages, config.memory_days
                )
                store.conn.execute(
                    "INSERT INTO messages VALUES (?,?,?,?)",
                    (sent.message_id, root[0], answer, time.time()),
                )
        except Exception as exc:
            store.error("comment", str(exc) if isinstance(exc, ModelError) else "COMMENT_FAILED")

    return router
