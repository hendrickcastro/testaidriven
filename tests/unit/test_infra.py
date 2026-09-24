import logging
from pathlib import Path

import pytest

from aidriven.adapters.artifact_stores import LocalArtifactStore
from aidriven.adapters.repo_sqlite import SqliteRepository
from aidriven.adapters.secrets import MemorySecretStore, mask, resolve_api_key
from aidriven.domain.models import (
    Capabilities,
    GenerationParams,
    ModelProfile,
    Pricing,
    ReasoningMode,
    Task,
)
from aidriven.logging_setup import LogCapture, LogEntry
from aidriven.services.task_io import export_tasks, import_tasks, load_seed_tasks, parse_tasks


def test_sqlite_repository_roundtrip_and_filters(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "db.sqlite")
    a = Task(slug="a", title="A", category="x", prompt="p", enabled=True)
    b = Task(slug="b", title="B", category="y", prompt="p", enabled=False)
    repo.save_many([a, b])
    assert repo.get(Task, a.id) == a.model_copy(update={"updated_at": repo.get(Task, a.id).updated_at})  # type: ignore[union-attr]
    assert [t.slug for t in repo.find(Task, where={"category": "y"})] == ["b"]
    assert [t.slug for t in repo.find(Task, where={"enabled": True})] == ["a"]
    assert repo.delete_where(Task, "category", "x") == 1
    repo.set_setting("k", {"v": 1})
    assert repo.get_setting("k") == {"v": 1}


def test_local_store_rejects_escape(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    loc = store.put("run", "res", "../../x.py", b"data", "text/plain")
    assert store.get(loc) == b"data"
    with pytest.raises(ValueError):
        store.get("../../outside.txt")


def test_secret_resolution_prefers_saved_then_env(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemorySecretStore()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key-123456789")
    assert resolve_api_key(store, "k", "anthropic") == ("env-key-123456789", "env")
    store.set("k", "saved-key-123456789")
    assert resolve_api_key(store, "k", "anthropic") == ("saved-key-123456789", "saved")
    assert mask("saved-key-123456789").endswith("6789")
    assert "saved" not in mask("saved-key-123456789")


def test_profile_param_validation() -> None:
    p = ModelProfile(
        name="x",
        provider_id="p",
        model="m",
        capabilities=Capabilities(reasoning=ReasoningMode.EFFORT, reasoning_efforts=["low", "high"], temperature=False),
        params=GenerationParams(temperature=0.2, reasoning_effort="max"),
    )
    problems = p.validate_params()
    assert any("temperature" in x for x in problems)
    assert any("reasoning_effort" in x for x in problems)


def test_pricing_cost() -> None:
    price = Pricing(input_per_mtok=5, output_per_mtok=25, cached_input_per_mtok=0.5)
    assert price.cost(1_000_000, 100_000, cached_tokens=500_000) == pytest.approx(2.5 + 0.25 + 2.5)


def test_log_capture_extracts_index_urls() -> None:
    cap = LogCapture()
    url = "https://console.firebase.google.com/v1/r/project/algoritxia/firestore/indexes?create_composite=ClBwcm9q"
    from datetime import UTC, datetime

    cap.add(
        LogEntry(datetime.now(UTC), "ERROR", "x", f"400 The query requires an index. You can create it here: {url}")
    )
    cap.add(LogEntry(datetime.now(UTC), "ERROR", "x", f"again {url}"))
    hints = cap.hints()
    assert len(hints) == 1
    assert hints[0].url == url
    assert hints[0].count == 2


def test_task_export_import_roundtrip(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "db.sqlite")
    t = Task(slug="s", title="T", category="c", prompt="p")
    text = export_tasks([t])
    assert parse_tasks(text)[0].slug == "s"
    assert import_tasks(repo, text) == (1, 0)
    assert import_tasks(repo, text) == (0, 1)
    assert repo.find(Task)[0].version == 2


def test_seed_tasks_are_valid() -> None:
    tasks = load_seed_tasks()
    slugs = [t.slug for t in tasks]
    assert len(slugs) == len(set(slugs))
    for t in tasks:
        assert t.checks, f"{t.slug} has no checks"
        assert t.rubric, f"{t.slug} has no rubric"


def test_logging_setup_is_idempotent(tmp_path: Path) -> None:
    from aidriven.logging_setup import setup_logging

    a = setup_logging(tmp_path)
    b = setup_logging(tmp_path)
    assert a is b
    logging.getLogger("x").info("hello")


def test_redaction_masks_keys_and_private_keys() -> None:
    from aidriven.logging_setup import redact

    text = (
        "auth failed for sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123 and AIzaSyA1234567890abcdefghijklmnopqrstu "
        '{"private_key": "-----BEGIN PRIVATE KEY-----\nMIIEv\n-----END PRIVATE KEY-----"} api_key=supersecretvalue1'
    )
    out = redact(text)
    for secret in ("abcdefghijklmnopqrstuvwxyz0123", "AIzaSyA1234567890", "MIIEv", "supersecretvalue1"):
        assert secret not in out
    assert "[REDACTED]" in out


async def test_flaky_fake_recovers_on_retry() -> None:
    from aidriven.adapters.llm.fake_adapter import FakeAdapter
    from aidriven.domain.models import ProviderConnection, ProviderKind
    from aidriven.ports import LLMRequest, Message

    adapter = FakeAdapter(ProviderConnection(kind=ProviderKind.FAKE, name="f", max_retries=1), None)
    adapter_sleep = __import__("aidriven.adapters.llm.base", fromlist=["asyncio"]).asyncio
    original = adapter_sleep.sleep

    async def no_sleep(_: float) -> None:
        return None

    adapter_sleep.sleep = no_sleep  # type: ignore[assignment]
    try:
        retried = 0
        for i in range(30):
            req = LLMRequest(model="flaky", messages=[Message(role="user", text=f"q{i}")], params=GenerationParams())
            resp = await adapter.generate(req)
            retried += resp.retries
        assert retried > 0  # some prompts failed once and recovered
    finally:
        adapter_sleep.sleep = original  # type: ignore[assignment]


def test_encrypted_repo_secret_store(tmp_path: Path) -> None:
    from aidriven.adapters.secrets import EncryptedRepoSecretStore, derive_fernet_key

    repo = SqliteRepository(tmp_path / "db.sqlite")
    cache = MemorySecretStore()
    store = EncryptedRepoSecretStore(repo, derive_fernet_key("sa-private-key"), cache=cache)
    store.set("provider:p1:api_key", "sk-ant-verysecretvalue")
    raw = repo.get_setting("secret__provider:p1:api_key")
    assert raw is not None and "verysecret" not in str(raw)  # only ciphertext at rest
    assert cache.get("provider:p1:api_key") == "sk-ant-verysecretvalue"
    # another machine: empty local cache, same derivation material → decrypts from the repository
    other = EncryptedRepoSecretStore(repo, derive_fernet_key("sa-private-key"), cache=MemorySecretStore())
    assert other.get("provider:p1:api_key") == "sk-ant-verysecretvalue"
    # wrong key → not revealed
    wrong = EncryptedRepoSecretStore(repo, derive_fernet_key("other"), cache=MemorySecretStore())
    assert wrong.get("provider:p1:api_key") is None
    store.delete("provider:p1:api_key")
    assert repo.get_setting("secret__provider:p1:api_key") is None


def test_firestore_nested_array_encoding_roundtrip() -> None:
    from aidriven.adapters.repo_firestore import decode_nested, encode_nested

    doc = {"checks": [{"expected": [[1, "a"], [2, "b"]], "files": {"x": [1, 2]}}], "plain": [1, 2, 3]}
    enc = encode_nested(doc)

    def has_nested_array(v: object) -> bool:
        if isinstance(v, list):
            return any(isinstance(i, list) for i in v) or any(has_nested_array(i) for i in v)
        if isinstance(v, dict):
            return any(has_nested_array(i) for i in v.values())
        return False

    assert not has_nested_array(enc)
    assert decode_nested(enc) == doc


def test_sanitize_ssl_env_drops_missing_cert_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from aidriven.__main__ import sanitize_ssl_env

    good = tmp_path / "ca.pem"
    good.write_text("x")
    monkeypatch.setenv("SSL_CERT_FILE", r"C:\nope\miniconda3/ssl/cacert.pem")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(good))
    removed = sanitize_ssl_env()
    import os

    assert "SSL_CERT_FILE" not in os.environ and os.environ["REQUESTS_CA_BUNDLE"] == str(good)
    assert removed and removed[0].startswith("SSL_CERT_FILE=")
