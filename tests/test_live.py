import os

import pytest

from wallapop_tracker import WallapopClient


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_public_profile_endpoints():
    profile_url = os.getenv("WALLAPOP_TEST_PROFILE_URL")
    if not profile_url:
        pytest.skip("WALLAPOP_TEST_PROFILE_URL is not configured")
    async with WallapopClient() as client:
        user_id = await client.resolve_user_id(profile_url)
        profile = await client.get_profile(user_id)
        stats = await client.get_profile_stats(user_id)
        reviews = await client.get_review_summary(user_id)
        items = await client.get_all_items(user_id)
    assert profile.user_id == user_id
    assert stats is not None
    assert reviews is not None
    assert isinstance(items, list)
