import datetime

DATETIME_FORMATS = ["%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S.%f"]


def date_to_iso8601(dt: datetime.datetime) -> str:
    return dt.strftime(DATETIME_FORMATS[0])


def iso8601_to_date(value: str) -> datetime.datetime:
    err: "ValueError | None" = None
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError as exc:
            err = exc
    assert err is not None
    raise err