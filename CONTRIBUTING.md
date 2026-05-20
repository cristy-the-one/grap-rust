# Contributing to grap

Thank you for your interest in contributing to **grap**!

## Code of Conduct

This project follows the [Rust Code of Conduct](https://www.rust-lang.org/policies/code-of-conduct).

## How to Contribute

### Reporting Bugs

- Use the [GitHub issue tracker](https://github.com/yourname/grap-rust/issues)
- Include a minimal reproducible example when possible
- Mention your Rust version (`rustc --version`) and platform

### Suggesting Features

We welcome ideas! Please open an issue first to discuss the feature before submitting a large pull request.

### Pull Requests

1. Fork the repository and create your branch from `master`.
2. Make your changes.
3. Run the following commands and ensure they pass:

   ```bash
   cargo fmt -- --check
   cargo clippy --all-targets --all-features -- -D warnings
   cargo test --all-features
   ```

4. Update documentation if your change affects public API or behavior.
5. Submit a pull request with a clear description.

### Development Tips

- The MSRV is **1.81**.
- Most development happens with `--all-features`.
- The key architectural claims (bitmask pruning, ghost edges, etc.) are validated in `benches/graph_benches.rs`.
- When adding new public API, please add documentation and consider whether it should be feature-gated.

### Commit Style

We follow conventional commit style loosely:

- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation only
- `refactor:`, `test:`, `chore:`, etc.

## Development Environment

This project includes a Dev Container configuration for easy onboarding.

### Using VS Code + Dev Containers

1. Install the "Dev Containers" extension in VS Code.
2. Open the project folder in VS Code.
3. When prompted, click **"Reopen in Container"**.
4. The container will automatically:
   - Install the Rust toolchain defined in `rust-toolchain.toml`
   - Install recommended extensions (rust-analyzer, etc.)
   - Run `cargo check --all-features` on first start

### GitHub Codespaces

You can also click **"Code → Codespaces → Create codespace on master"** on GitHub. It will use the same devcontainer configuration.

### Manual Setup

If you prefer not to use containers:

```bash
rustup toolchain install stable
rustup component add rustfmt clippy
cargo check --all-features
```

## License

By contributing, you agree that your contributions will be licensed under the same license as the project (MIT OR Apache-2.0).

---

Thank you for helping make grap better!