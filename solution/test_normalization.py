from normalization import (
    normalize_header,
    normalize_phone,
    normalize_date,
    normalize_decimal,
    normalize_enum,
)


def test_normalize_header():
    assert normalize_header(" Customer_Name ") == "customer name"


def test_normalize_phone():
    assert normalize_phone("+91 98765 43210") == "+919876543210"
    assert normalize_phone("09876543210") == "+919876543210"
    assert normalize_phone("9876543210") == "+919876543210"


def test_invalid_phone():
    try:
        normalize_phone("12345")
        assert False
    except ValueError:
        pass


def test_normalize_date():
    assert normalize_date("2026-11-13") == "2026-11-13"
    assert normalize_date("13/11/2026") == "2026-11-13"
    assert normalize_date("13-Nov-2026") == "2026-11-13"


def test_normalize_decimal():
    assert str(normalize_decimal("₹27,554")) == "27554.00"
    assert str(normalize_decimal("INR 6850")) == "6850.00"


def test_normalize_enum():
    assert normalize_enum("HI", ["en", "hi", "ta"]) == "hi"
