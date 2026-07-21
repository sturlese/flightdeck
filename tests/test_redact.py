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


def test_iban_does_not_eat_a_following_uppercase_token():
    # The IBAN token must not swallow a neighbouring short uppercase/digit word,
    # e.g. a currency code — that deletes legitimate text from the payload.
    result = redact("Refund to ES91 2100 0418 4502 0005 1332 EUR now")
    assert result.text == "Refund to [REDACTED:iban] EUR now"
    assert result.by_kind["iban"] == 1


def test_iban_does_not_eat_a_following_short_code():
    result = redact("pay ES91 2100 0418 4502 0005 1332 ID 5")
    assert result.text == "pay [REDACTED:iban] ID 5"


def test_credit_card_requires_luhn():
    valid = redact("card 4111 1111 1111 1111")  # Luhn-valid test number
    invalid = redact("order id 4111 1111 1111 1112")  # fails Luhn — not a card
    assert "[REDACTED:card]" in valid.text
    assert "[REDACTED:card]" not in invalid.text


def test_card_redaction_preserves_trailing_separator():
    # The card token must not swallow the separator that belongs to the
    # surrounding text, or the redaction merges into the next word.
    assert redact("card 4111 1111 1111 1111 and b").text == "card [REDACTED:card] and b"


def test_two_adjacent_cards_keep_their_separator():
    result = redact("4111 1111 1111 1111 5500 0000 0000 0004")
    assert result.by_kind["card"] == 2
    assert result.text == "[REDACTED:card] [REDACTED:card]"


def test_card_followed_by_short_number_still_redacted():
    # A valid card next to a short group (a CVV, an amount) forms a 17–19 digit
    # run the greedy pattern grabs whole; the combined string fails Luhn, so the
    # card must not be allowed to leak — redact it and leave the short number.
    result = redact("charge 4111 1111 1111 1111 123 now")
    assert "[REDACTED:card]" in result.text
    assert "4111 1111 1111 1111" not in result.text  # the card must not leak in clear text
    assert result.text == "charge [REDACTED:card] 123 now"
    assert result.by_kind["card"] == 1


def test_card_preceded_by_short_number_still_redacted():
    # Same failure when the short group leads the card instead of trailing it.
    result = redact("ref 12 4111 1111 1111 1111 done")
    assert result.text == "ref 12 [REDACTED:card] done"
    assert result.by_kind["card"] == 1


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
