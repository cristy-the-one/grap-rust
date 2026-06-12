# Prebuilt pygrap wheel

`bootstrap.ps1` installs a prebuilt `pygrap` wheel from this directory so the
demo machine does not need a Rust toolchain.

**The wheel is committed to git** (`.gitignore` carves out
`demo/win/wheels/pygrap-*.whl` from the general `wheels/` ignore) so that a
plain `git clone` is enough to run the demo. If it is ever absent,
`bootstrap.ps1` builds it automatically when a Rust + maturin toolchain is
present, or tells you how to build it elsewhere.

## Build it (Python 3.13 x64 + Rust + maturin)

From the repository root:

```powershell
python -m pip install maturin
maturin build --release -m crates\pygrap\Cargo.toml --out demo\win\wheels
```

This produces `pygrap-<version>-cp313-cp313-win_amd64.whl` here. Copy that
file alongside this README on any distribution media you hand to the demo
machine. `bootstrap.ps1` picks up the first `pygrap-*win_amd64.whl` it finds.

The bundled wheel targets **CPython 3.13 / win_amd64**. A different Python
minor version or architecture needs its own rebuild.
