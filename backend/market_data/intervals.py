_INTERVAL_TO_SECONDS = {
    "1": 60,
    "3": 180,
    "5": 300,
    "15": 900,
    "30": 1800,
    "60": 3600,
    "120": 7200,
    "240": 14400,
    "D": 86400,
}


def interval_to_seconds(interval: str) -> int:
    normalized_interval = interval.strip()
    if normalized_interval not in _INTERVAL_TO_SECONDS:
        raise ValueError(f"Unknown interval: {interval!r}")
    return _INTERVAL_TO_SECONDS[normalized_interval]


def interval_to_minutes(interval: str) -> int:
    return interval_to_seconds(interval) // 60
