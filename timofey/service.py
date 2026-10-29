import asyncio
import json
import time

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from .provider import ModelError


class Service:
    def __init__(self, config, store, desk, bot):
        self.config, self.store, self.desk, self.bot = config, store, desk, bot
        self.work = asyncio.Lock()
        self.stop_event = asyncio.Event()

    def enabled(self):
        return not self.store.get("paused", False)

    async def generate(self, forced=None, topic_request=None):
        if not self.enabled():
            raise ModelError("BOT_PAUSED")
        if self.work.locked():
            raise ModelError("BUSY_TRY_LATER")
        async with self.work:
            return await self.desk.generate(forced, topic_request=topic_request)

    async def edit(self, event_id, text):
        from .news import validate_text

        row = self.store.event(event_id)
        if not row or row["state"] != "draft":
            raise ModelError("NOT_EDITABLE")
        if row["kind"] != "delirium":
            validate_text(text, (self.config.api_key, self.config.bot_token))
        if row["kind"] == "delirium":
            from .news import validate_delirium

            validate_delirium(text, (self.config.api_key, self.config.bot_token))
        # An in-flight edit cannot be published with the old content by another update.
        changed = self.store.conn.execute(
            "UPDATE events SET state='editing' WHERE id=? AND state='draft'", (event_id,)
        ).rowcount
        if not changed:
            raise ModelError("NOT_EDITABLE")
        try:
            if row["kind"] == "delirium":
                await self.desk.audit(
                    text, row["location"] + " " + row["topic"], for_comment=True
                )
            else:
                await self.desk.audit(text, row["location"] + " " + row["topic"])
            data = json.loads(row["payload"])
            data["text"] = text
            self.store.conn.execute(
                "UPDATE events SET body=?,payload=?,state='draft' WHERE id=? AND state='editing'",
                (text, json.dumps(data, ensure_ascii=False), event_id),
            )
        finally:
            self.store.conn.execute(
                "UPDATE events SET state='draft' WHERE id=? AND state='editing'", (event_id,)
            )

    async def publish(self, event_id):
        if not self.enabled():
            raise ModelError("BOT_PAUSED")
        row = self.store.event(event_id)
        if not row or not self.store.claim_publication(event_id):
            raise ModelError("ALREADY_SENT_OR_NOT_APPROVED")
        try:
            result = await self.bot.send_message(
                self.config.channel_id, row["body"], parse_mode=None
            )
        except (TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter):
            # Explicit refusal is unambiguous: allow a later owner retry.
            self.store.conn.execute(
                "UPDATE events SET state='draft' WHERE id=? AND state='sending'", (event_id,)
            )
            raise ModelError("TELEGRAM_REJECTED_PUBLICATION") from None
        except BaseException:
            # Timeout after send may mean it was published. Manual reconciliation only.
            self.store.conn.execute(
                "UPDATE events SET state='uncertain' WHERE id=? AND state='sending'", (event_id,)
            )
            self.store.error("publication", "DELIVERY_UNCERTAIN")
            raise ModelError("DELIVERY_UNCERTAIN_CHECK_CHANNEL") from None
        self.store.published(event_id, result.message_id)
        return result.message_id

    async def scheduler_tick(self, now=None):
        now = time.time() if now is None else now
        if not self.enabled() or not self.store.get("autopost", False):
            return
        due = self.store.get("next_post", 0)
        if now < due:
            return
        interval = self.store.get("interval", self.config.interval_minutes)
        # Advance before await. Outage/restart does not cause a backlog flood.
        self.store.set("next_post", now + interval * 60)
        if self.store.get("mode", "manual") == "manual":
            pending = self.store.conn.execute(
                "SELECT id FROM events WHERE state='draft' LIMIT 1"
            ).fetchone()
            if pending:
                return
        try:
            event_id = await self.generate()
            if self.store.get("mode", "manual") == "automatic":
                await self.publish(event_id)
            else:
                await self.preview(event_id)
        except Exception as exc:
            code = str(exc) if isinstance(exc, ModelError) else "SCHEDULER_ERROR"
            self.store.error("scheduler", code)
            try:
                await self.bot.send_message(self.config.owner_id, "Автопост: " + code)
            except Exception:
                self.store.error("owner_notification", "TELEGRAM_UNAVAILABLE")

    async def scheduler(self):
        while not self.stop_event.is_set():
            await self.scheduler_tick()
            self.store.prune(self.config.memory_days)
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=5)
            except TimeoutError:
                pass

    async def preview(self, event_id):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        row = self.store.event(event_id)
        if not row:
            raise ModelError("DRAFT_NOT_FOUND")
        markup = (
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Опубликовать", callback_data=f"publish:{event_id}"
                        ),
                        InlineKeyboardButton(
                            text="Удалить черновик", callback_data=f"discard:{event_id}"
                        ),
                    ]
                ]
            )
            if row["state"] == "draft"
            else None
        )
        await self.bot.send_message(
            self.config.owner_id,
            f"Черновик #{event_id} | {row['kind']} | {row['state']}\n\n{row['body']}",
            reply_markup=markup,
            parse_mode=None,
        )
