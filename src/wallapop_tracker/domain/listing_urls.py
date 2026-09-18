"""Parsing of Wallapop listing references used by the CLI."""

import re
from urllib.parse import urlparse


def parse_listing_reference(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("listing URL or item ID is required")
    if "://" not in value:
        return value
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc not in {
        "es.wallapop.com",
        "www.wallapop.com",
    }:
        raise ValueError("listing URL must be an HTTPS Wallapop item URL")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[-2] != "item":
        raise ValueError("listing URL must contain /item/")
    slug = parts[-1]
    match = re.search(r"(?:^|-)([A-Za-z0-9]+)$", slug)
    if match is None:
        raise ValueError("listing URL does not contain an item ID")
    return match.group(1)
