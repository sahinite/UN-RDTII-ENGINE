import os
from dataclasses import dataclass, field

from src.queue.env_builder import PROVIDER_KEYS, build_subprocess_env


@dataclass
class FakeCtx:
    user_hash: str
    secrets: dict[str, str] = field(default_factory=dict)


def test_provider_keys_stripped_from_base_env():
    ctx = FakeCtx(user_hash="abc")
    base = {"OPENAI_API_KEY": "parent"}
    result = build_subprocess_env(ctx, run_id="r1", base_env=base)
    assert "OPENAI_API_KEY" not in result


def test_user_secrets_override_stripped_parent():
    ctx = FakeCtx(user_hash="abc", secrets={"OPENAI_API_KEY": "user"})
    base = {"OPENAI_API_KEY": "parent"}
    result = build_subprocess_env(ctx, run_id="r1", base_env=base)
    assert result["OPENAI_API_KEY"] == "user"


def test_all_provider_keys_stripped():
    ctx = FakeCtx(user_hash="abc")
    base = {k: "parent" for k in PROVIDER_KEYS}
    result = build_subprocess_env(ctx, run_id="r1", base_env=base)
    for k in PROVIDER_KEYS:
        assert k not in result


def test_non_provider_env_preserved():
    ctx = FakeCtx(user_hash="abc")
    base = {"PATH": "/usr/bin", "HOME": "/home/x"}
    result = build_subprocess_env(ctx, run_id="r1", base_env=base)
    assert result["PATH"] == "/usr/bin"
    assert result["HOME"] == "/home/x"


def test_output_dir_set():
    ctx = FakeCtx(user_hash="deadbeefcafebabe")
    result = build_subprocess_env(ctx, run_id="run-abc", base_env={}, outputs_root="outputs")
    assert result["RDTII_OUTPUT_DIR"] == "outputs/deadbeefcafebabe/run-abc"


def test_log_dir_set():
    ctx = FakeCtx(user_hash="deadbeefcafebabe")
    result = build_subprocess_env(ctx, run_id="run-abc", base_env={}, logs_root="logs")
    assert result["RDTII_LOG_DIR"] == "logs/deadbeefcafebabe/run-abc"


def test_output_dir_respects_custom_root():
    ctx = FakeCtx(user_hash="abc")
    result = build_subprocess_env(ctx, run_id="r1", base_env={}, outputs_root="/tmp/foo")
    assert result["RDTII_OUTPUT_DIR"].startswith("/tmp/foo/")


def test_base_env_not_mutated():
    ctx = FakeCtx(user_hash="abc", secrets={"OPENAI_API_KEY": "user"})
    base = {"OPENAI_API_KEY": "parent", "PATH": "/usr/bin"}
    snapshot = dict(base)
    build_subprocess_env(ctx, run_id="r1", base_env=base)
    assert base == snapshot


def test_os_environ_not_mutated():
    ctx = FakeCtx(user_hash="abc", secrets={"OPENAI_API_KEY": "user"})
    snapshot = dict(os.environ)
    build_subprocess_env(ctx, run_id="r1")
    assert dict(os.environ) == snapshot


def test_default_base_env_is_os_environ_snapshot(monkeypatch):
    monkeypatch.setenv("SOME_RANDOM_VAR_XYZ", "hello")
    ctx = FakeCtx(user_hash="abc")
    result = build_subprocess_env(ctx, run_id="r1")
    assert result.get("SOME_RANDOM_VAR_XYZ") == "hello"


def test_user_secrets_added_when_not_in_parent():
    ctx = FakeCtx(user_hash="abc", secrets={"OPENAI_API_KEY": "user"})
    result = build_subprocess_env(ctx, run_id="r1", base_env={})
    assert result["OPENAI_API_KEY"] == "user"


def test_returns_only_strings():
    ctx = FakeCtx(user_hash="abc", secrets={"SOME_INT_KEY": 12345})
    result = build_subprocess_env(ctx, run_id="r1", base_env={})
    assert result["SOME_INT_KEY"] == "12345"
    assert isinstance(result["SOME_INT_KEY"], str)
    for v in result.values():
        assert isinstance(v, str)
