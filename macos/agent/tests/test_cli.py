from rachel.secrets import load


def test_secrets_parse(tmp_path):
    f = tmp_path / ".secrets.env"
    f.write_text("# comment\nAZURE_SPEECH_KEY=abc123\nAZURE_REGION=westeurope\n")
    d = load(str(f))
    assert d["AZURE_SPEECH_KEY"] == "abc123"
    assert d["AZURE_REGION"] == "westeurope"
    assert "comment" not in d


def test_secrets_missing_file_is_empty(tmp_path):
    assert load(str(tmp_path / "nope.env")) == {}


def test_secrets_ignores_blank_and_malformed(tmp_path):
    f = tmp_path / ".secrets.env"
    f.write_text("\n\nNOEQUALS\nK = v \n")
    d = load(str(f))
    assert d == {"K": "v"}
