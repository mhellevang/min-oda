"""llm.py: provider-valg via env og JSON-ekstraksjon fra LLM-svar."""

from min_oda import llm


def test_provider_none_slaar_av(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    assert llm.active_provider() == "none"
    assert not llm.enabled()
    assert llm.chat("sys", "user") is None


def test_provider_eksplisitt(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "codex_cli")
    assert llm.active_provider() == "codex_cli"


def test_extract_json_rent():
    assert llm.extract_json('[{"a": 1}]') == [{"a": 1}]


def test_extract_json_fenced():
    assert llm.extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_med_prat():
    assert llm.extract_json('Her er svaret:\n[{"a": 1}]\nHåper det hjelper!') == [{"a": 1}]


def test_extract_json_indre_array_i_objekt():
    # Ytterste klamme vinner — ikke den indre arrayen.
    assert llm.extract_json('{"actions": []}') == {"actions": []}


def test_extract_json_ubrukelig():
    assert llm.extract_json("beklager, jeg kan ikke") is None
    assert llm.extract_json(None) is None
    assert llm.extract_json("") is None
