from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "British Museum Agent"
    app_env: str = "local"
    data_dir: Path = Path("./data")
    raw_spanish_path: Path = Path("./data/raw/spanish")
    index_path: Path = Path("./data/processed/knowledge_index.json")
    sqlite_path: Path = Path("./data/sqlite/app.db")
    chroma_path: Path = Path("./data/chroma")
    chroma_collection_name: str = "british_museum_es"
    retrieval_backend: Literal["chroma", "lexical"] = "chroma"
    embedding_model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    reranker_model_name: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    retrieval_candidate_k: int = 10
    llm_provider: str = "gemini"
    gemini_model: str = "gemini-2.5-flash"
    google_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    langsmith_api_key: SecretStr | None = None
    langsmith_tracing: bool = False
    langsmith_project: str = "british-museum-agent"
    phoenix_enabled: bool = False
    phoenix_collector_endpoint: str = "http://localhost:4317"
    phoenix_api_key: SecretStr | None = None
    phoenix_project_name: str = "british-museum-agent"
    staff_demo_username: str = Field(default="staff@example.com", max_length=254)
    staff_demo_password: SecretStr | None = None
    jwt_secret: SecretStr | None = None
    jwt_expiration_minutes: int = Field(default=60, gt=0)
    mcp_server_url: str = "http://localhost:8001/mcp"
    mcp_internal_token: SecretStr | None = None

    @property
    def resolved_gemini_api_key(self) -> str | None:
        for secret in (self.google_api_key, self.gemini_api_key):
            if secret is not None and secret.get_secret_value().strip():
                return secret.get_secret_value().strip()
        return None

    @property
    def langsmith_enabled(self) -> bool:
        return bool(
            self.langsmith_tracing
            and self.langsmith_api_key is not None
            and self.langsmith_api_key.get_secret_value().strip()
        )

    @property
    def resolved_phoenix_collector_endpoint(self) -> str:
        return self.phoenix_collector_endpoint.strip() or "http://localhost:4317"

    @property
    def phoenix_api_key_value(self) -> str | None:
        if self.phoenix_api_key is None:
            return None
        value = self.phoenix_api_key.get_secret_value().strip()
        return value or None

    @property
    def jwt_secret_value(self) -> str | None:
        if self.jwt_secret is None:
            return None
        value = self.jwt_secret.get_secret_value().strip()
        return value or None

    @property
    def staff_demo_password_value(self) -> str | None:
        if self.staff_demo_password is None:
            return None
        value = self.staff_demo_password.get_secret_value()
        return value if value else None

    @property
    def mcp_internal_token_value(self) -> str | None:
        if self.mcp_internal_token is None:
            return None
        value = self.mcp_internal_token.get_secret_value().strip()
        return value or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
