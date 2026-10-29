import os
import re


def valid_model_id(model):
    return bool(isinstance(model, str) and len(model) <= 150 and re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._:-]+", model))
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    bot_token: str = field(default="", repr=False)
    api_key: str = field(default="", repr=False)
    owner_id: int = 7787565361
    channel_id: int = 0
    discussion_id: int = 0
    channel_username: str = "@SAR_Kefirstan"
    discussion_username: str = "@Kefirstano"
    model: str = "mistralai/mistral-large-2512"
    data_dir: Path = Path("runtime")
    knowledge_file: Path = Path("knowledge/canon.jsonl")
    max_output_tokens: int = 3072
    daily_token_limit: int = 500000
    requests_per_minute: int = 6
    user_cooldown: int = 180
    global_reply_cooldown: int = 20
    memory_messages: int = 20
    memory_days: int = 30
    reply_probability: float = 0.15
    interval_minutes: int = 180

    def __post_init__(self):
        if self.owner_id <= 0 or self.channel_id > 0 or self.discussion_id > 0:
            raise ValueError("Invalid numeric Telegram IDs")
        if self.channel_id and self.channel_id == self.discussion_id:
            raise ValueError("Channel and discussion must differ")
        if not valid_model_id(self.model):
            raise ValueError("AI_MODEL must be a full xKiro model ID")
        if not 0 <= self.reply_probability <= 1:
            raise ValueError("REPLY_PROBABILITY must be 0..1")
        if not 128 <= self.max_output_tokens <= 8192:
            raise ValueError("MAX_OUTPUT_TOKENS must be 128..8192")
        for value in (
            self.daily_token_limit,
            self.requests_per_minute,
            self.user_cooldown,
            self.global_reply_cooldown,
            self.memory_messages,
            self.memory_days,
            self.interval_minutes,
        ):
            if value <= 0:
                raise ValueError("Limits must be positive")

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv

        load_dotenv()
        fields = {
            "bot_token": ("BOT_TOKEN", str),
            "api_key": ("XKIRO_API_KEY", str),
            "owner_id": ("OWNER_ID", int),
            "channel_id": ("CHANNEL_ID", int),
            "discussion_id": ("DISCUSSION_ID", int),
            "channel_username": ("CHANNEL_USERNAME", str),
            "discussion_username": ("DISCUSSION_USERNAME", str),
            "data_dir": ("DATA_DIR", Path),
            "knowledge_file": ("KNOWLEDGE_FILE", Path),
            "max_output_tokens": ("MAX_OUTPUT_TOKENS", int),
            "daily_token_limit": ("DAILY_TOKEN_LIMIT", int),
            "requests_per_minute": ("REQUESTS_PER_MINUTE", int),
            "user_cooldown": ("USER_COOLDOWN_SECONDS", int),
            "global_reply_cooldown": ("GLOBAL_REPLY_COOLDOWN_SECONDS", int),
            "memory_messages": ("MEMORY_MESSAGES", int),
            "memory_days": ("MEMORY_DAYS", int),
            "reply_probability": ("REPLY_PROBABILITY", float),
            "interval_minutes": ("POST_INTERVAL_MINUTES", int),
        }
        values = {k: cast(os.environ[n]) for k, (n, cast) in fields.items() if os.environ.get(n)}
        model = os.environ.get("AI_MODEL") or os.environ.get("MINIMAX_MODEL")
        if model:
            values["model"] = model
        return cls(**values)
