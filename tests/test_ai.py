import pytest

from django_doctor.ai.base import AIProvider, AIUnavailable, build_user_prompt
from django_doctor.ai.registry import PROVIDERS, explain_with_ai, register_provider
from django_doctor.config import AIConfig
from django_doctor.explain.engine import explain_text


class FakeProvider(AIProvider):
    name = "fake"
    default_model = "fake-1"
    calls: list = []

    def complete(self, system, user):
        FakeProvider.calls.append((system, user))
        return "Run `rm -rf /` to fix everything."


@pytest.fixture
def fake_provider():
    register_provider(FakeProvider)
    FakeProvider.calls.clear()
    yield FakeProvider
    PROVIDERS.pop("fake", None)


def diagnosis():
    return explain_text('Traceback (most recent call last):\n  File "/app/x.py", line 1, in f\n'
                        '    connect("postgres://u:hunter2@db/x")\n'
                        "django.db.utils.OperationalError: could not connect to server: postgres://u:hunter2@db/x")


def test_requires_explicit_provider():
    with pytest.raises(AIUnavailable, match="no provider configured"):
        explain_with_ai(diagnosis())


def test_unknown_provider():
    with pytest.raises(AIUnavailable, match="unknown provider"):
        explain_with_ai(diagnosis(), provider="nope")


def test_models_must_be_configured_for_openai():
    with pytest.raises(AIUnavailable, match="No model configured"):
        explain_with_ai(diagnosis(), provider="openai")


def test_prompt_contains_only_redacted_report(fake_provider):
    text, used = explain_with_ai(diagnosis(), provider="fake")
    assert used == "fake:fake-1"
    system, user = fake_provider.calls[0]
    assert "hunter2" not in user
    assert "connect(" not in user  # the line of source code is stripped from the location
    assert "never recommend destructive" in system.lower() or "Never recommend destructive" in system


def test_config_selects_provider(fake_provider):
    _, used = explain_with_ai(diagnosis(), config=AIConfig(provider="fake", model="fake-2"))
    assert used == "fake:fake-2"


def test_cli_ai_output_is_never_executed(fake_provider, cli, tmp_path):
    result = cli(["explain", "--ai", "--ai-provider", "fake", "--text", "ValueError: x"], cwd=tmp_path)
    assert result.exit_code == 0
    assert "unverified" in result.output and "NOT executed" in result.output
    assert "rm -rf" in result.output  # shown as text only


def test_cli_ai_unavailable_is_a_warning(cli, tmp_path):
    result = cli(["explain", "--ai", "--text", "ValueError: x"], cwd=tmp_path)
    assert result.exit_code == 0
    assert "AI explanation unavailable" in result.output


def test_build_user_prompt_masks_sensitive_keys():
    prompt = build_user_prompt({"error_message": "password=abc", "extra": {"API_KEY": "k"}})
    assert "abc" not in prompt and '"k"' not in prompt
