from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    tg_api_id: str
    tg_api_hash: str
    local_bot_api_url: str = "http://telegram-bot-api:8081"

    reddit_client_id: str
    reddit_client_secret: str
    reddit_username: str
    reddit_password: str
    reddit_user_agent: str = "reddit_tg_bot/1.0"

    source_channel_id: int
    dest_channel_id: int

    trusted_user_ids: str = ""

    send_delay_seconds: float = 1.2
    worker_concurrency: int = 4

    db_path: str = "./data/bot.db"
    tmp_dir: str = "./tmp"

    @property
    def trusted_ids(self) -> set[int]:
        return {
            int(raw.strip())
            for raw in self.trusted_user_ids.split(",")
            if raw.strip()
        }


settings = Settings()
