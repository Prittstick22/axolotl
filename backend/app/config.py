"""Settings - owner: Dev 2. Every variable in SHARED_CONTRACTS §10.

Non-secret values get sensible defaults; secrets default to empty so the app always
boots and reports dependencies as unavailable rather than crashing.
No connection logic lives here.
"""

from functools import lru_cache
from typing import Any, Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RunnerMode = Literal["trusted_docker", "disabled"]


class Settings(BaseSettings):
    """Mirrors `.env.example` / SHARED_CONTRACTS §10 one-for-one."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Gemini (Dev 1) ---
    gemini_api_key: str = ""
    gemini_model: str = "gemini-4-argon"
    gemini_thinking_level: str = "medium"
    gemini_embedding_model: str = ""
    embedding_dimensions: int | None = None

    # Additive under SHARED_CONTRACTS §11: optional, defaults to the behaviour
    # callers already had. Off skips descriptions and embeddings at import, which
    # keeps the test suite and CI from making one Gemini call per chunk.
    index_on_import: bool = True

    # --- MongoDB Atlas (Dev 3) ---
    mongodb_uri: str = ""
    mongodb_database: str = "axolotl"

    # --- Sandbox runner (Dev 4) ---
    runner_mode: RunnerMode = "trusted_docker"
    runner_image: str = "axolotl-runner:latest"
    # Additive under SHARED_CONTRACTS §11 (#22): one image per interpreter, built from
    # the same Dockerfile. `python2_to_3` baselines on 2.7 and verifies on 3.14.
    runner_image_py27: str = "axolotl-runner:py2.7"
    runner_image_py314: str = "axolotl-runner:py3.14"
    allow_arbitrary_code_execution: bool = False
    workspace_root: str = "./.axolotl_workspaces"

    # --- Frontend / orchestration (Dev 2) ---
    frontend_origin: str = "http://localhost:5173"
    max_patch_attempts: int = 3
    max_tool_calls_per_unit: int = 25

    @model_validator(mode="before")
    @classmethod
    def _blank_means_default(cls, data: Any) -> Any:
        """A blank value in `.env` means "use the default", not "" and never a crash.

        `.env.example` ships blank placeholders - `EMBEDDING_DIMENSIONS=`,
        `GEMINI_EMBEDDING_MODEL=` - because nobody knows those values until the
        cluster and the model are confirmed. Without this, copying the example
        verbatim (which the README tells you to do) makes the app refuse to boot:

            ValidationError: embedding_dimensions
              Input should be a valid integer, unable to parse string as an integer

        Dropping empty keys lets the declared default apply instead, so a blank
        numeric field falls back rather than exploding.
        """
        if isinstance(data, dict):
            return {
                key: value
                for key, value in data.items()
                if not (isinstance(value, str) and value.strip() == "")
            }
        return data

    def secret_values(self) -> list[str]:
        """Non-empty secret values, for `redact()` in orchestration/events.py (§7).

        Anything returned here is stripped from event payloads and log excerpts.
        """
        candidates = [self.gemini_api_key, self.mongodb_uri]
        return [v for v in candidates if v and len(v) >= 8]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
