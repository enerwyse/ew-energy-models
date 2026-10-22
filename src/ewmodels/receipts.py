"""Evidence receipts: every computed number carries its provenance.

A receipt answers the questions an auditor, M&V reviewer or incentive program
asks before trusting a figure: what data went in (hash, span, resolution),
which sufficiency checks passed, which simplifying assumptions apply, and
which code version produced it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

import pandas as pd

T = TypeVar("T")


@dataclass(frozen=True)
class Check:
    """A pass/fail data-sufficiency or validity test."""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class Receipt:
    model: str
    model_version: str
    input_sha256: str
    n_records: int
    resolution: str | None
    period_start: str | None
    period_end: str | None
    parameters: dict[str, Any] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    created_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def valid(self) -> bool:
        """True only if every check passed."""
        return all(c.passed for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["valid"] = self.valid
        return d

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), default=str, **kwargs)

    def summary(self) -> str:
        lines = [f"{self.model} v{self.model_version} — {'VALID' if self.valid else 'NOT VALID'}"]
        lines += [f"  [{'x' if c.passed else ' '}] {c.name}: {c.detail}" for c in self.checks]
        lines += [f"  caveat: {c}" for c in self.caveats]
        return "\n".join(lines)


@dataclass
class Result(Generic[T]):
    value: T
    receipt: Receipt


def hash_data(obj: pd.Series | pd.DataFrame) -> str:
    """Stable SHA-256 over index and values (order-sensitive)."""
    h = hashlib.sha256()
    h.update(pd.util.hash_pandas_object(obj, index=True).values.tobytes())
    return h.hexdigest()


def make_receipt(
    model: str,
    data: pd.Series | pd.DataFrame,
    *,
    parameters: dict[str, Any] | None = None,
    checks: list[Check] | None = None,
    caveats: list[str] | None = None,
) -> Receipt:
    from ewmodels import __version__
    from ewmodels.resolution import describe_resolution

    idx = data.index
    is_time = isinstance(idx, pd.DatetimeIndex) and len(idx) > 0
    return Receipt(
        model=model,
        model_version=__version__,
        input_sha256=hash_data(data),
        n_records=len(data),
        resolution=describe_resolution(idx) if is_time else None,
        period_start=str(idx.min()) if is_time else None,
        period_end=str(idx.max()) if is_time else None,
        parameters=parameters or {},
        checks=checks or [],
        caveats=caveats or [],
    )
