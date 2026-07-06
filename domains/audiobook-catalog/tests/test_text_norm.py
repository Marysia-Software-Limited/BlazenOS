from audiobook_catalog.text_norm import _fold, _stem_phrase, _stem_token


def test_fold_strips_polish_diacritics():
    assert _fold("Trójkę") == "trojke"


def test_stem_phrase_pads_and_stems():
    assert _stem_phrase("Trójka") == " trojk "


def test_stem_token_keeps_short_tokens():
    assert _stem_token("kot") == "kot"
    assert _stem_token("kota") == "kot"
