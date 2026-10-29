import argparse
import asyncio
import logging
import os
from pathlib import Path

from .config import Config
from .db import Store
from .knowledge import Knowledge, import_xml
from .lock import InstanceLock
from .news import NewsDesk
from .provider import MiniMax, ModelError
from .service import Service
from .telegram import Access, build_router, resolve_chats


async def run():
    from aiogram import Bot, Dispatcher
    from aiogram.client.session.aiohttp import AiohttpSession

    config = Config.from_env()
    if not config.bot_token:
        raise SystemExit("BOT_TOKEN missing. Configure environment, then restart.")
    config.data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(config.data_dir, 0o700)
    lock = InstanceLock(config.data_dir / "bot.lock")
    store = Store(config.data_dir / "state.sqlite3")
    bot = Bot(
        config.bot_token,
        session=AiohttpSession(proxy=os.getenv("TELEGRAM_PROXY") or os.getenv("HTTPS_PROXY")),
    )
    model = None
    tasks = []
    try:
        config = await resolve_chats(bot, config)
        saved_model = store.get("model")
        if saved_model and saved_model.startswith("minimax/"):
            config.model = saved_model
        kb = Knowledge(store)
        kb.load(config.knowledge_file)
        model = MiniMax(config, store)
        desk = NewsDesk(store, kb, model, config)
        service = Service(config, store, desk, bot)
        dispatcher = Dispatcher()
        dispatcher.update.outer_middleware(Access(config, store))
        dispatcher.include_router(build_router(service))

        # Error handler deliberately avoids raw exception logging (Telegram URLs include token).
        @dispatcher.errors()
        async def errors(event):
            store.error("telegram_dispatch", "UNHANDLED_UPDATE")
            return True

        await bot.send_message(
            config.owner_id,
            f"Тимофей запущен\nКанал ID: {config.channel_id}\nЧат ID: {config.discussion_id}\n"
            f"Режим: {store.get('mode', 'manual')}\nАвтопост: {store.get('autopost', False)}\n/admin",
        )
        tasks = [
            asyncio.create_task(service.scheduler()),
            asyncio.create_task(
                dispatcher.start_polling(
                    bot, allowed_updates=["message", "callback_query"], close_bot_session=False
                )
            ),
            asyncio.create_task(service.stop_event.wait()),
        ]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if model:
            await model.close()
        await bot.session.close()
        store.close()
        lock.close()


def main():
    parser = argparse.ArgumentParser(description="Тимофей AI")
    sub = parser.add_subparsers(dest="command")
    imp = sub.add_parser("import-wiki")
    imp.add_argument("source", type=Path)
    imp.add_argument("--output", type=Path, default=Path("knowledge/canon.jsonl"))
    sub.add_parser("check")
    args = parser.parse_args()
    if args.command == "import-wiki":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        records = import_xml(args.source, args.output)
        print(f"Imported {len(records)} canon chunks")
        return
    if args.command == "check":
        config = Config.from_env()
        store = Store(":memory:")
        kb = Knowledge(store)
        kb.load(config.knowledge_file)
        print(f"Canon chunks: {store.conn.execute('SELECT count(*) FROM canon').fetchone()[0]}")
        print("BOT_TOKEN:", "set" if config.bot_token else "missing")
        print("XKIRO_API_KEY:", "set" if config.api_key else "missing")
        print("MiniMax:", config.model)
        print("Check is offline. No Telegram or model requests were made.")
        store.close()
        return
    # Silence dependency loggers rather than risk credential-bearing request URLs.
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        code = str(exc) if isinstance(exc, ModelError) else type(exc).__name__
        raise SystemExit("Startup stopped: " + code) from None


if __name__ == "__main__":
    main()
