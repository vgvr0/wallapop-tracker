"""Capture the current authorized public API shape into dated RAW fixtures."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wallapop_tracker import WallapopClient

OUTPUT = Path("tests/fixtures/raw/2026-09")
PROFILES = {
    "example": "https://es.wallapop.com/user/joseantoniol-64102686",
    "reviewer_a": "https://es.wallapop.com/user/martag-16085078",
    "reviewer_b": "https://es.wallapop.com/user/pablos-457067972",
}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    observations: list[dict[str, Any]] = []
    async with WallapopClient(min_interval=0.5) as client:
        for label, url in PROFILES.items():
            user_id = await client.resolve_user_id(url)
            stats = await client.get_profile_stats(user_id)
            reviews = await client.get_review_summary(user_id)
            observations.append(
                {
                    "label": label,
                    "url": url,
                    "user_id": user_id,
                    "stats": stats.model_dump(mode="json"),
                    "reviews": reviews.model_dump(mode="json"),
                }
            )
            if label == "example":
                profile_raw = await client._request(
                    "GET", f"{client.base_url}/api/v3/users/{user_id}", expect_json=True
                )
                stats_raw = await client._request(
                    "GET", f"{client.base_url}/api/v3/users/{user_id}/stats", expect_json=True
                )
                reviews_raw = await client._request(
                    "GET",
                    f"{client.base_url}/api/v3/users/{user_id}/reviews/summary",
                    expect_json=True,
                )
                page_1 = await client._request(
                    "GET", f"{client.base_url}/api/v3/users/{user_id}/items", expect_json=True
                )
                page_2 = await client._request(
                    "GET",
                    f"{client.base_url}/api/v3/users/{user_id}/items",
                    params={"since": page_1["meta"]["next"]},
                    expect_json=True,
                )
                write_json(OUTPUT / "profile.json", profile_raw)
                write_json(OUTPUT / "stats.json", stats_raw)
                write_json(OUTPUT / "reviews_summary.json", reviews_raw)
                write_json(OUTPUT / "items_page_1.json", page_1)
                write_json(OUTPUT / "items_page_2.json", page_2)
    write_json(
        OUTPUT / "metadata.json",
        {
            "captured_at": datetime.now(UTC).isoformat(),
            "wallapop_api": "unofficial/public frontend API",
            "authorization": "captured under user-provided Wallapop authorization",
            "endpoints": [
                "/api/v3/users/{user_id}",
                "/api/v3/users/{user_id}/stats",
                "/api/v3/users/{user_id}/reviews/summary",
                "/api/v3/users/{user_id}/items",
            ],
            "profiles": observations,
            "sanitization": [
                "No cookies, tokens, authorization headers, or session identifiers were captured.",
                "The responses contain only public API response bodies.",
            ],
        },
    )


if __name__ == "__main__":
    asyncio.run(main())
