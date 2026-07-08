from flightdeck.redact import redact


def test_email_and_phone():
    result = redact("Contact ana.garcia@example.com or +34 612 345 678 today")
    assert "ana.garcia@example.com" not in result.text
    assert "612 345 678" not in result.text
    assert result.by_kind["email"] == 1
    assert result.by_kind["phone"] == 1


def test_iban():
    result = redact("Refund to ES91 2100 0418 4502 0005 1332 please")
    assert "[REDACTED:iban]" in result.text
    assert result.hits == 1


def test_credit_card_requires_luhn():
    valid = redact("card 4111 1111 1111 1111")  # Luhn-valid test number
    invalid = redact("order id 4111 1111 1111 1112")  # fails Luhn — not a card
    assert "[REDACTED:card]" in valid.text
    assert "[REDACTED:card]" not in invalid.text


def test_api_secret():
    result = redact("use key sk-abc123def456ghi789jkl012 for now")
    assert "[REDACTED:secret]" in result.text


def test_spanish_dni():
    result = redact("DNI 12345678Z attached")
    assert "[REDACTED:dni]" in result.text


def test_custom_pattern():
    result = redact("employee EMP-00423 requested it", extra_patterns=[r"\bEMP-\d{5}\b"])
    assert "EMP-00423" not in result.text
    assert result.hits == 1


def test_clean_text_untouched():
    text = "Ship 3 features in Q3 for 12 customers."
    result = redact(text)
    assert result.text == text
    assert result.hits == 0
