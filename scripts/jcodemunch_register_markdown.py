"""Register a lightweight Markdown language with jCodemunch before indexing.

jCodemunch 1.108.x does not ship .md in LANGUAGE_EXTENSIONS. This script patches the
installed package in-process so index_folder indexes prose docs with heading symbols
and full-text search.

Usage:
  python scripts/jcodemunch_register_markdown.py
  python scripts/jcodemunch_register_markdown.py --index E:/projects/grap-rust
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _parse_markdown_symbols(source_bytes: bytes, filename: str):
    from jcodemunch_mcp.parser.symbols import Symbol, make_symbol_id, compute_content_hash

    text = source_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()
    symbols: list[Symbol] = []

    # File-level anchor so empty docs still appear in the index.
    doc_name = Path(filename).stem
    doc_line = f"document {doc_name}"
    doc_bytes = doc_line.encode("utf-8")
    symbols.append(
        Symbol(
            id=make_symbol_id(filename, doc_name, "type"),
            file=filename,
            name=doc_name,
            qualified_name=doc_name,
            kind="type",
            language="markdown",
            signature=doc_line,
            summary=f"Markdown document: {doc_name}",
            line=1,
            end_line=max(1, len(lines)),
            byte_offset=0,
            byte_length=len(source_bytes),
            content_hash=compute_content_hash(source_bytes),
        )
    )

    for i, line in enumerate(lines, start=1):
        m = _HEADING_RE.match(line)
        if not m:
            continue
        level = len(m.group(1))
        title = m.group(2).strip()
        if not title:
            continue
        qname = title
        sig = f"{'#' * level} {title}"
        line_bytes = line.encode("utf-8")
        symbols.append(
            Symbol(
                id=make_symbol_id(filename, qname, "type"),
                file=filename,
                name=title,
                qualified_name=qname,
                kind="type",
                language="markdown",
                signature=sig,
                summary=f"Markdown heading (h{level})",
                line=i,
                end_line=i,
                byte_offset=0,
                byte_length=len(line_bytes),
                content_hash=compute_content_hash(line_bytes),
            )
        )

    return symbols


def register_markdown_language() -> None:
    from jcodemunch_mcp.parser import languages as lang_mod
    from jcodemunch_mcp.parser import extractor as ext_mod
    from jcodemunch_mcp.parser.languages import LanguageSpec

    if "markdown" in lang_mod.LANGUAGE_REGISTRY:
        return

    spec = LanguageSpec(
        ts_language="markdown",
        symbol_node_types={},
        name_fields={},
        param_fields={},
        return_type_fields={},
        docstring_strategy="preceding_comment",
        decorator_node_type=None,
        container_node_types=[],
        constant_patterns=[],
        type_patterns=[],
    )
    lang_mod.LANGUAGE_REGISTRY["markdown"] = spec
    for ext in (".md", ".mdx", ".markdown"):
        lang_mod.LANGUAGE_EXTENSIONS[ext] = "markdown"

    original = ext_mod.parse_file

    def parse_file(content, filename, language, source_bytes=None, repo=None):
        if language == "markdown":
            if source_bytes is None:
                source_bytes = content.encode("utf-8")
            return _parse_markdown_symbols(source_bytes, filename)
        return original(content, filename, language, source_bytes=source_bytes, repo=repo)

    ext_mod.parse_file = parse_file


def index_repo(path: Path) -> dict:
    register_markdown_language()
    from jcodemunch_mcp.tools.index_folder import index_folder

    return index_folder(
        str(path.resolve()),
        use_ai_summaries=False,
        incremental=True,
        paths=[
            "docs",
            "mcp-server",
            "src",
            "crates",
            "examples",
            "tests",
            "README.md",
            "DEMO.md",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "CLAUDE.md",
            "core_graph_design.md",
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", metavar="PATH", help="Index this repo after registering markdown")
    args = parser.parse_args()
    register_markdown_language()
    print("jCodemunch markdown language registered (.md, .mdx, .markdown → heading symbols + text search)")
    if args.index:
        result = index_repo(Path(args.index))
        print(
            f"Indexed {result.get('file_count', '?')} files, "
            f"{result.get('symbol_count', '?')} symbols"
        )
        warnings = result.get("warnings") or []
        if warnings:
            print("Warnings:", *warnings[:10], sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main())