"""Application settings loaded from environment variables or a .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    shopify_store_url: str = Field(..., alias="SHOPIFY_STORE_URL")
    shopify_access_token: str = Field(..., alias="SHOPIFY_ACCESS_TOKEN")
    shopify_api_version: str = Field("2025-07", alias="SHOPIFY_API_VERSION")
    shopify_product_limit: int = Field(3, alias="SHOPIFY_PRODUCT_LIMIT", ge=1, le=50)

    gemini_api_key: str = Field(..., alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-2.5-flash", alias="GEMINI_MODEL")

    output_dir: Path = Field(Path("./generated_videos"), alias="OUTPUT_DIR")
    font_path: str = Field("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", alias="FONT_PATH")
    daily_run_hour_utc: int = Field(9, alias="DAILY_RUN_HOUR_UTC", ge=0, le=23)
    enable_scheduler: bool = Field(True, alias="ENABLE_SCHEDULER")

    @field_validator("shopify_store_url")
    @classmethod
    def normalize_store_url(cls, value: str) -> str:
        value = value.strip().removeprefix("https://").removeprefix("http://")
        return value.rstrip("/")

    @property
    def shopify_graphql_endpoint(self) -> str:
        return f"https://{self.shopify_store_url}/admin/api/{self.shopify_api_version}/graphql.json"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    return settings
