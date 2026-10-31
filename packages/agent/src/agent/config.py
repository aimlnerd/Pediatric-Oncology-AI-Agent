"""Runtime settings, read from environment variables."""

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    project: str | None
    search_location: str
    data_store_id: str
    guard_model: str
    main_model: str
    default_center: str
    search_page_size: int
    max_searches_per_turn: int
    max_clarification_rounds: int


@lru_cache
def get_settings() -> Settings:
    return Settings(
        project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        search_location=os.environ.get("BSPHO_SEARCH_LOCATION", "eu"),
        data_store_id=os.environ.get("BSPHO_DATA_STORE_ID", "bspho-protocols-eu"),
        guard_model=os.environ.get("BSPHO_GUARD_MODEL", "gemini-2.5-flash-lite"),
        main_model=os.environ.get("BSPHO_MAIN_MODEL", "gemini-2.5-flash"),
        default_center=os.environ.get("BSPHO_DEFAULT_CENTER", "UZG"),
        search_page_size=int(os.environ.get("BSPHO_SEARCH_PAGE_SIZE", "8")),
        max_searches_per_turn=3,
        max_clarification_rounds=2,
    )
