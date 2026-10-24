"""Search-only settings - owner: Dev 3. Issue #11.

Two settings that only the search layer reads, kept here rather than in `app/config.py`
(Dev 2's) so they can land without a shared-file change. They come from the same
environment and `.env` files as `Settings`, which ignores keys it does not know, so
both read one `.env` without stepping on each other.

  INDEX_DESCRIPTIONS        false by default. Off, an import embeds each chunk from its
                            source, signature, identifiers and docstring, with no model
                            description - no `generate_content` call at all.
                            `POST /repositories/{id}/index` always adds descriptions.
  GEMINI_DESCRIPTION_MODEL  the model that writes descriptions. Unset, it is
                            `GEMINI_MODEL`, as before; set it to a cheap Flash model so
                            descriptions never spend the agent's (Argon's) quota.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import get_settings


class SearchSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    index_descriptions: bool = False
    gemini_description_model: str = ""


@lru_cache
def search_settings() -> SearchSettings:
    return SearchSettings()


def description_model() -> str:
    """The model that writes chunk descriptions."""
    return search_settings().gemini_description_model or get_settings().gemini_model
