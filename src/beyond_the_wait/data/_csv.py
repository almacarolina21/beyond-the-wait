"""Read public CSV files exactly as published.

Every column is read as TEXT and nothing is auto-converted to "missing".
Why: by default pandas would turn "Not Available" or a blank cell into NaN,
and would turn facility ID "010001" into the number 10001. We want to see
the publisher's exact value first, then decide -- explicitly -- what it means.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from beyond_the_wait.data.errors import PublicDataError


def read_text_csv(path: str | Path, required_columns: Iterable[str], label: str) -> pd.DataFrame:
    """Read a CSV with all values as strings; fail clearly if columns are missing."""
    path = Path(path)
    if not path.is_file():
        raise PublicDataError(f"{label}: file not found: {path}")
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    except (UnicodeDecodeError, pd.errors.ParserError) as exc:
        raise PublicDataError(f"{label}: could not parse {path}: {exc}") from exc
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        raise PublicDataError(
            f"{label}: {path.name} is missing expected column(s) {missing}. "
            "The publisher may have changed the file layout; review before updating code."
        )
    return df.apply(lambda col: col.str.strip())
