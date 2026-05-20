# Publishing Checklist for grap 0.1.0

This document outlines the steps to publish a high-quality first release to crates.io.

## Pre-release

- [ ] All tests pass: `cargo test --all-features`
- [ ] Clippy is clean: `cargo clippy --all-targets --all-features -- -D warnings`
- [ ] Documentation builds without warnings: `cargo doc --no-deps --all-features`
- [ ] `cargo fmt -- --check` passes
- [ ] Benchmarks compile: `cargo check --benches --features "fulltext,sharding"`
- [ ] CHANGELOG.md is up to date for this release
- [ ] README.md looks good and has working examples
- [ ] MSRV in `Cargo.toml` (`rust-version`) and CI match
- [ ] `RepairReport` and `Telemetry` are properly documented with examples

## Metadata

- [ ] `Cargo.toml` has:
  - Good `description`
  - Accurate `keywords` and `categories`
  - `repository` and `documentation` links
  - `license` + `readme`
  - `rust-version` set correctly
- [ ] `LICENSE-MIT` and `LICENSE-APACHE` files exist (or combined notice)
- [ ] `Cargo.lock` is up to date

## Release Process

1. **Create a Git tag**
   ```bash
   git tag -a v0.1.0 -m "Release v0.1.0"
   git push origin v0.1.0
   ```

2. **Create a GitHub Release**
   - Go to Releases → Draft new release
   - Use the tag `v0.1.0`
   - Paste the relevant section from `CHANGELOG.md`
   - This will automatically trigger the `release.yml` workflow (if configured)

3. **Publish to crates.io**
   The GitHub Actions release workflow should handle this automatically.
   Alternatively, do it manually:

   ```bash
   cargo publish
   ```

4. **Post-release**

   - [ ] Verify the crate appears on https://crates.io/crates/grap
   - [ ] Verify docs are published on https://docs.rs/grap
   - [ ] Announce the release (if desired)
   - [ ] Consider creating a `0.1.x` maintenance branch

## Future Improvements (post 0.1.0)

- Add `cargo-release` or `release-plz` for automated versioning
- Set up semantic-release or similar
- Add a `SECURITY.md` file
- Consider adding a `CONTRIBUTORS` or `AUTHORS` file

---

**Last updated**: April 2026 (Phase 5 completion)