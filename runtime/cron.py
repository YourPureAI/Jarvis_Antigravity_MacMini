"""Minimal 5-field cron parser (stdlib). Supports *, lists, ranges, steps, month/day names.
Day-of-month and day-of-week use standard Vixie-cron OR semantics when both are restricted."""
from __future__ import annotations

from datetime import datetime, timedelta

_NAMES = {
    3: {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)},
    4: {d: i for i, d in enumerate("sun mon tue wed thu fri sat".split())},
}
_BOUNDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
_ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}


def _val(tok: str, idx: int) -> int:
    tok = tok.lower()
    if idx in _NAMES and tok in _NAMES[idx]:
        return _NAMES[idx][tok]
    return int(tok)


def _field(spec: str, idx: int) -> set[int]:
    lo, hi = _BOUNDS[idx]
    result: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
            if step < 1:
                raise ValueError("step must be >= 1")
        if part in ("*", "?"):
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            a, b = _val(x, idx), _val(y, idx)
        else:
            a = _val(part, idx)
            b = hi if step > 1 else a
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise ValueError(f"value out of range in '{spec}'")
        result.update(range(a, b + 1, step))
    if idx == 4 and 7 in result:  # 7 == Sunday
        result.discard(7)
        result.add(0)
    return result


class Cron:
    def __init__(self, expr: str):
        expr = _ALIASES.get(expr.strip().lower(), expr.strip())
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError("cron expression needs 5 fields: min hour dom month dow")
        self.expr = expr
        self.minute, self.hour, self.dom, self.month, self.dow = (_field(p, i) for i, p in enumerate(parts))
        self.dom_any = parts[2] in ("*", "?")
        self.dow_any = parts[4] in ("*", "?")

    def _day_ok(self, d: datetime) -> bool:
        if d.month not in self.month:
            return False
        dom_ok = d.day in self.dom
        dow_ok = (d.isoweekday() % 7) in self.dow
        if self.dom_any and self.dow_any:
            return True
        if self.dom_any:
            return dow_ok
        if self.dow_any:
            return dom_ok
        return dom_ok or dow_ok

    def next_after(self, after: datetime) -> datetime:
        """First matching minute strictly after `after` (timezone-aware local time)."""
        # Iterate in naive local wall-clock time so DST shifts do not skew matching.
        base = after.astimezone().replace(tzinfo=None) if after.tzinfo else after
        t = (base + timedelta(minutes=1)).replace(second=0, microsecond=0)
        limit = t + timedelta(days=366 * 5)
        while t < limit:
            if not self._day_ok(t):
                t = (t + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if t.hour not in self.hour:
                t = (t + timedelta(hours=1)).replace(minute=0)
                continue
            if t.minute not in self.minute:
                t += timedelta(minutes=1)
                continue
            return t.astimezone()
        raise ValueError(f"cron '{self.expr}' never fires")
