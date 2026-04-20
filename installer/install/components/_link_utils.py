from __future__ import annotations

from collections.abc import Mapping


def extract_module_url(module_download_links: Mapping[str, object] | None, module_key: str) -> str:
    if not isinstance(module_download_links, Mapping):
        return ""

    link_entry = module_download_links.get(module_key)
    if not isinstance(link_entry, Mapping):
        return ""

    return str(link_entry.get("url", "") or "").strip()
