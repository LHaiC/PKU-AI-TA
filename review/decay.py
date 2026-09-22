"""Late-submission score decay.

Reads a deadline rule file (one `hours_late_upper_bound factor` per line)
and computes a per-student scaling factor from the submission timestamp.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class DecayBucket:
    """One tier of the rule: hours_late in (lo, hi] -> factor."""
    lo: float          # exclusive lower bound, hours
    hi: float | None   # inclusive upper bound, hours; None = unbounded
    factor: float
    label: str         # display text, e.g. "(0, 24]h" or "> 72h"


def parse_rule(path: Path) -> list[DecayBucket]:
    """Parse a rule file like:

        # <hours late upper bound> <factor>
        24   0.8
        48   0.6
        72   0.4

    Each line is `<upper bound hours> <factor>`; `#` starts a comment and
    blank lines are ignored. Bounds may be `24`, `24h`, or `inf`; factors
    may be `0.8` or `80%`. Tiers are matched in ascending bound order;
    submissions later than the largest finite bound score 0 (unless an
    `inf` row sets a different factor).
    """
    tiers: list[tuple[float | None, float]] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = re.split(r"[\s=]+", line)
        if len(parts) != 2:
            raise ValueError(
                f"{path}:{lineno}: expected '<hours> <factor>', got {raw.strip()!r}")
        bound_s, factor_s = parts
        bound_s = bound_s.lower().rstrip("h")
        try:
            hi = None if bound_s in ("inf", "*") else float(bound_s)
            factor = float(factor_s.rstrip("%"))
        except ValueError:
            raise ValueError(f"{path}:{lineno}: cannot parse {raw.strip()!r}")
        if factor > 1.0:  # written as percent
            factor /= 100.0
        if not 0.0 <= factor <= 1.0 or (hi is not None and hi <= 0):
            raise ValueError(f"{path}:{lineno}: bad values in {raw.strip()!r}")
        tiers.append((hi, factor))
    if not tiers:
        raise ValueError(f"no decay rules parsed from {path}")
    tiers.sort(key=lambda t: math.inf if t[0] is None else t[0])

    buckets: list[DecayBucket] = []
    lo = 0.0
    for hi, factor in tiers:
        label = f"({lo:g}, {hi:g}]h" if hi is not None else f"> {lo:g}h"
        buckets.append(DecayBucket(lo=lo, hi=hi, factor=factor, label=label))
        if hi is None:
            break
        lo = hi
    if buckets[-1].hi is not None:  # beyond the last finite bound: rejected
        hi = buckets[-1].hi
        buckets.append(DecayBucket(lo=hi, hi=None, factor=0.0,
                                   label=f"> {hi:g}h"))
    return buckets


def decay_factor(hours_late: float, buckets: list[DecayBucket]) -> DecayBucket | None:
    """Return the matching bucket; None/1.0 when submitted on time."""
    if hours_late <= 0:
        return None
    for b in buckets:
        if hours_late > b.lo and (b.hi is None or hours_late <= b.hi):
            return b
    return None


def parse_time(text: str) -> datetime | None:
    """Blackboard shows '2026-09-12 22:32:57' (site-local time)."""
    text = (text or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


@dataclass
class DecayRow:
    student_id: str
    student_name: str
    submitted_at: str
    hours_late: float
    factor: float
    raw_score: float
    final_score: float
    reason: str
    extra_times: list[str]  # other attempts' timestamps, for transparency


def compute_decay(
    records,                      # list[ReviewRecord]
    submit_times: dict[str, dict],  # sid -> {"newest": str, "all": [str,...]}
    due: datetime,
    buckets: list[DecayBucket],
) -> list[DecayRow]:
    """One DecayRow per record. Multi-attempt policy: the newest attempt's
    timestamp decides lateness; earlier attempt times are kept in extra_times
    so a reviewer can spot deadline-straddling resubmissions."""
    rows: list[DecayRow] = []
    for r in records:
        sid = r.result.student_id
        raw = r.base_score  # pre-decay score — keeps re-runs idempotent
        info = submit_times.get(sid, {})
        t = parse_time(info.get("newest", ""))
        extra = [x for x in info.get("all", []) if x != info.get("newest", "")]
        if t is None:
            rows.append(DecayRow(sid, r.result.student_name, info.get("newest", "?"),
                                 0.0, 1.0, raw, raw, "提交时间缺失，按准时计", extra))
            continue
        hours = (t - due).total_seconds() / 3600.0
        bucket = decay_factor(hours, buckets)
        if bucket is None:
            rows.append(DecayRow(sid, r.result.student_name, info["newest"],
                                 max(hours, 0.0), 1.0, raw, raw, "准时提交", extra))
        else:
            final = round(raw * bucket.factor, 1)
            rows.append(DecayRow(
                sid, r.result.student_name, info["newest"], hours, bucket.factor,
                raw, final,
                f"迟交 {hours:.1f} 小时，落入 {bucket.label} 档，系数 {bucket.factor:g}"
                + ("（>72h，按规定不予接受）" if bucket.factor == 0 else ""),
                extra,
            ))
    rows.sort(key=lambda d: (d.factor, d.student_id))
    return rows
