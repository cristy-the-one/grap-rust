# Maintainer script: (re)build the bundled pygrap wheel into packaging\wheels\.
# Requires the Rust toolchain (https://rustup.rs) + maturin. The resulting wheel
# is what scripts\install.ps1 ships to fresh systems (no Rust needed there).
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Out = Join-Path $Root "packaging\wheels"

python -m pip install -q -U maturin
python -m maturin build --release -m (Join-Path $Root "crates\pygrap\Cargo.toml") -o $Out
Write-Host "`nBuilt wheel(s) in $Out :" -ForegroundColor Green
Get-ChildItem (Join-Path $Out "pygrap-*.whl") | ForEach-Object { Write-Host "  $($_.Name)" }
