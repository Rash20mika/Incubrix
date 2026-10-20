from __future__ import annotations

import os

import pytest

from incubrix.config import Settings
from incubrix.images import UnsplashProvider


@pytest.mark.integration
def test_live_unsplash_search_when_explicitly_enabled() -> None:
    if os.getenv("RUN_LIVE_IMAGE_TESTS", "").lower() not in {"1", "true", "yes"}:
        pytest.skip("Set RUN_LIVE_IMAGE_TESTS=true to enable live provider checks.")
    settings = Settings.from_env()
    if not settings.unsplash_access_key:
        pytest.skip("UNSPLASH_ACCESS_KEY is not configured.")
    provider = UnsplashProvider(
        settings.unsplash_access_key,
        timeout=settings.image_provider_timeout_seconds,
        retries=0,
    )
    photos = provider.search("earth satellite agricultural land", limit=1)
    assert isinstance(photos, list)
    if photos:
        assert photos[0].provider == "Unsplash"
        assert photos[0].source_url.startswith("https://")
