"""bootstrap.ps1 must write a demo.env wiring the local Ollama stack."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WIN = Path(__file__).resolve().parents[1]
BOOT = WIN / "bootstrap.ps1"


def test_powershell_scripts_parse():
    """Every demo .ps1 must pass the PowerShell language parser.

    Guards against committing scripts that cannot run at all (a grep-only
    test cannot catch syntax errors).
    """
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh not on PATH")
    scripts = sorted(WIN.glob("*.ps1"))
    assert scripts, "no .ps1 scripts found next to the tests"
    check = (
        "$r = @(); Get-ChildItem -LiteralPath $env:DEMO_WIN -Filter *.ps1 | ForEach-Object { "
        "$e = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$null, [ref]$e); "
        "$r += @{ file = $_.Name; errors = @($e | ForEach-Object Message) } }; "
        "$r | ConvertTo-Json -Depth 4 -AsArray"
    )
    out = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", check],
        capture_output=True, text=True, timeout=120,
        env={"DEMO_WIN": str(WIN), "PATH": str(Path(pwsh).parent)},
    )
    assert out.returncode == 0, out.stderr
    results = json.loads(out.stdout)
    broken = {r["file"]: r["errors"] for r in results if r["errors"]}
    assert not broken, f"scripts with parse errors: {broken}"


def test_bootstrap_declares_expected_env_keys():
    text = BOOT.read_text(encoding="utf-8")
    for key in ("GRAP_LLM_BASE_URL", "GRAP_LLM_MODEL", "GRAP_EMBED_BACKEND",
                "GRAP_EMBED_MODEL"):
        assert key in text, f"bootstrap.ps1 must set {key} in demo.env"
    assert "qwen2.5:14b-instruct-q4_K_M" in text
    assert "bge-m3" in text
    assert "11434" in text
