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


def test_shortest_legal_iban_is_redacted():
    # NO9386011117947 — Norway, 15 characters, the shortest IBAN the standard
    # allows. The three-group branch needs 16, so this one used to travel to the
    # vendor intact while the run record reported zero redactions.
    result = redact("Refund to NO9386011117947 today")
    assert result.text == "Refund to [REDACTED:iban] today"
    assert result.by_kind["iban"] == 1


def test_iban_does_not_claim_a_token_too_short_to_be_one():
    # The 15-char branch ends in a MANDATORY contiguous 3-char tail, so short
    # alphanumeric noise cannot satisfy it: no legal IBAN is under 15 characters.
    result = redact("code AB12 CDEF GHIJ here")
    assert result.text == "code AB12 CDEF GHIJ here"
    assert "iban" not in result.by_kind


def test_a_reference_code_before_an_iban_does_not_swallow_it():
    # The 15-char branch must never match a span it does not mean to redact: a
    # discarded candidate is returned as-is and re.sub resumes PAST it, so a
    # 2-letter/2-digit reference code in front of an IBAN would consume the
    # IBAN's own country group and leak the rest.
    result = redact("PO12 ACME MT84 MALT011000012345MTLCAST001S")
    assert result.text == "PO12 ACME [REDACTED:iban]"
    assert result.by_kind["iban"] == 1


def test_a_long_group_run_containing_an_iban_is_still_claimed():
    # 35 compact characters — one past the standard's 34-char ceiling, and the
    # pattern's maximal reach. Length is enforced by the pattern rather than by a
    # post-match check precisely so this span keeps being claimed whole instead
    # of being handed back with the IBAN inside it.
    result = redact("Wire AB12 3456 7890 1234 5678 NO9386011117947 today")
    assert result.text == "Wire [REDACTED:iban] today"
    assert result.by_kind["iban"] == 1


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


def test_api_secret():
    result = redact("use key sk-abc123def456ghi789jkl012 for now")
    assert "[REDACTED:secret]" in result.text


def test_secret_does_not_eat_hyphenated_prose():
    # The secret pattern targets high-entropy vendor keys, not ordinary hyphenated
    # English that merely starts with a prefix word. Redacting prose deletes
    # legitimate text — the "mangles the prompt" failure the module avoids.
    for phrase in (
        "we use token-based-authentication-flow here",
        "a key-value-store-implementation detail",
        "the pk-anonymization-strategy applies",
    ):
        result = redact(phrase)
        assert result.text == phrase, f"prose was redacted: {phrase!r}"
        assert "secret" not in result.by_kind
    # …but a real, high-entropy key with the same prefix is still caught.
    hit = redact("rotate sk-live-abc123def456ghi789 now")
    assert "[REDACTED:secret]" in hit.text


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
