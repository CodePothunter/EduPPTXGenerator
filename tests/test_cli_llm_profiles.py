from pathlib import Path

from click.testing import CliRunner

from edupptx.cli import main


class _CapturingAgent:
    configs = []

    def __init__(self, config):
        self.config = config
        self.__class__.configs.append(config)

    def run(self, *_args, **_kwargs):
        session_dir = Path(self.config.output_dir) / "session_test"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "plan.json").write_text("{}", encoding="utf-8")
        return session_dir


def _clear_llm_env(monkeypatch):
    for name in [
        "GEN_MODEL",
        "GEN_APIKEY",
        "GEN_BASE_URL",
        "API_BASE_URL",
        "LLM_PROVIDER",
        "GEN_THINKING",
        "GEN_REASONING_EFFORT",
        "DEEPSEEK_MODEL",
        "DEEPSEEK_GEN_MODEL",
        "DEEPSEEK_APIKEY",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_GEN_APIKEY",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_GEN_BASE_URL",
        "DEEPSEEK_LLM_PROVIDER",
        "DEEPSEEK_THINKING",
        "DEEPSEEK_GEN_THINKING",
        "DEEPSEEK_REASONING_EFFORT",
        "DEEPSEEK_GEN_REASONING_EFFORT",
        "DOUBAO_MODEL",
        "DOUBAO_GEN_MODEL",
        "DOUBAO_APIKEY",
        "DOUBAO_API_KEY",
        "DOUBAO_GEN_APIKEY",
        "DOUBAO_BASE_URL",
        "DOUBAO_GEN_BASE_URL",
        "DOUBAO_LLM_PROVIDER",
        "DOUBAO_GEN_THINKING",
        "DOUBAO_GEN_REASONING_EFFORT",
        "ARK_MODEL",
        "ARK_API_KEY",
        "ARK_BASE_URL",
    ]:
        monkeypatch.delenv(name, raising=False)


def test_plan_llm_deepseek_profile_overrides_generation_config(tmp_path, monkeypatch):
    _clear_llm_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "GEN_MODEL=ep-doubao",
                "GEN_APIKEY=generic-key",
                "GEN_BASE_URL=https://ark.cn-beijing.volces.com/api/v3",
                "DEEPSEEK_GEN_MODEL=deepseek-v4-pro-from-env",
                "DEEPSEEK_GEN_APIKEY=deepseek-key",
                "DEEPSEEK_GEN_BASE_URL=https://api.deepseek.com/v1/chat/completions",
                "DEEPSEEK_LLM_PROVIDER=chat",
                "DEEPSEEK_GEN_THINKING=enabled",
                "DEEPSEEK_GEN_REASONING_EFFORT=high",
            ]
        ),
        encoding="utf-8",
    )
    _CapturingAgent.configs = []
    monkeypatch.setattr("edupptx.cli.PPTXAgent", _CapturingAgent)

    result = CliRunner().invoke(
        main,
        [
            "plan",
            "topic",
            "--llm",
            "deepseek",
            "--env-file",
            str(env_file),
            "--output",
            str(tmp_path / "output"),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    config = _CapturingAgent.configs[-1]
    assert config.llm_model == "deepseek-v4-pro-from-env"
    assert config.llm_api_key == "deepseek-key"
    assert config.llm_base_url == "https://api.deepseek.com/v1"
    assert config.llm_provider == "chat"
    assert config.llm_thinking == "enabled"
    assert config.llm_reasoning_effort == "high"


def test_plan_llm_doubao_profile_preserves_thinking_and_reasoning(tmp_path, monkeypatch):
    _clear_llm_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "GEN_MODEL=deepseek-v4-pro",
                "GEN_APIKEY=generic-key",
                "GEN_BASE_URL=https://api.deepseek.com",
                "GEN_THINKING=enabled",
                "GEN_REASONING_EFFORT=high",
                "DOUBAO_GEN_MODEL=ep-20260224095549-rxjvq",
                "DOUBAO_GEN_APIKEY=doubao-key",
                "DOUBAO_GEN_BASE_URL=https://ark.cn-beijing.volces.com/api/v3",
                "DOUBAO_LLM_PROVIDER=chat",
            ]
        ),
        encoding="utf-8",
    )
    _CapturingAgent.configs = []
    monkeypatch.setattr("edupptx.cli.PPTXAgent", _CapturingAgent)

    result = CliRunner().invoke(
        main,
        [
            "plan",
            "topic",
            "--llm",
            "doubao",
            "--env-file",
            str(env_file),
            "--output",
            str(tmp_path / "output"),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    config = _CapturingAgent.configs[-1]
    assert config.llm_model == "ep-20260224095549-rxjvq"
    assert config.llm_api_key == "doubao-key"
    assert config.llm_base_url == "https://ark.cn-beijing.volces.com/api/v3"
    assert config.llm_provider == "chat"
    assert config.llm_thinking == ""
    assert config.llm_reasoning_effort == ""
