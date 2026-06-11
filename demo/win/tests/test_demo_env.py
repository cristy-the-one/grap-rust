"""bootstrap.ps1 must write a demo.env wiring the local Ollama stack."""

from pathlib import Path

BOOT = Path(__file__).resolve().parents[1] / "bootstrap.ps1"


def test_bootstrap_declares_expected_env_keys():
    text = BOOT.read_text(encoding="utf-8")
    for key in ("GRAP_LLM_BASE_URL", "GRAP_LLM_MODEL", "GRAP_EMBED_BACKEND",
                "GRAP_EMBED_MODEL"):
        assert key in text, f"bootstrap.ps1 must set {key} in demo.env"
    assert "qwen2.5:14b-instruct-q4_K_M" in text
    assert "bge-m3" in text
    assert "11434" in text
