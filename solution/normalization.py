import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def normalize_header(value: str) -> str:
    """Standardize a CSV column name."""
    value = value.strip().lower()
    return re.sub(r"[_\s]+", " ", value)


def normalize_phone(value: str) -> str:
    """Convert an Indian phone number into +91XXXXXXXXXX format."""
    digits = re.sub(r"\D", "", value)

    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) != 10 or digits[0] not in "6789":
        raise ValueError("invalid_phone")

    return "+91" + digits


def normalize_date(value: str) -> str:
    """Convert supported date formats into YYYY-MM-DD."""
    value = value.strip()

    formats = (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d-%b-%Y",
    )

    for date_format in formats:
        try:
            parsed = datetime.strptime(value, date_format)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue

    raise ValueError("invalid_date")


def normalize_decimal(value: str) -> Decimal:
    """Convert an Indian currency value into a two-decimal Decimal."""
    cleaned = value.strip()

    cleaned = re.sub(
        r"^(₹|rs\.?|inr)\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = cleaned.replace(",", "").strip()

    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError("invalid_decimal")

    if not amount.is_finite() or amount < 0:
        raise ValueError("invalid_decimal")

    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def normalize_enum(value: str, allowed_values: list[str]) -> str:
    """Match an enum value without considering letter case."""
    cleaned = value.strip().lower()
    allowed = {item.lower() for item in allowed_values}

    if cleaned not in allowed:
        raise ValueError("invalid_enum")

    return cleaned
