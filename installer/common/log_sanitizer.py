from __future__ import annotations

import re
from typing import Any


_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def redact_text(value: Any) -> str:
    return _URL_RE.sub("<url redacted>", str(value or ""))
