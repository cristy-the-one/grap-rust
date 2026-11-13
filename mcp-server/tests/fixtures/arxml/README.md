# ARXML test fixtures

Nine files from the **cantools** project's test corpus (`tests/files/arxml/`), MIT licence —
see `LICENSE-cantools.txt`. Upstream: https://github.com/cantools/cantools

They were copied byte-for-byte from the VSS-engine repository, where the ARXML reader design was
prototyped. `SHA256SUMS` records those bytes, and `test_arxml_fixtures.py` checks them.

**Do not edit, reformat or replace these files.** The acceptance values in
`docs/spec-arxml-reader.md` §8 were measured on exactly these bytes. A fresh download from
upstream may differ; if it does, it is a different fixture and the spec's numbers do not apply.
