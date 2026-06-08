"""Optional compact #MUNCH-style wire encoding for MCP tool responses.

JSON is the default; compact is opt-in per call until a client decoder ships.
The heavy lifting (legend interning + columnar packing) is done in the Rust
core via pygrap.wire.encode_tables when available; otherwise a pure-Python
fallback produces the same #MUNCH/1 format.
"""
import json

from .schemas import SCHEMAS

_FORMATS = ("auto", "compact", "json")
_BYTES_PER_TOKEN = 4


def default_format(repo=None) -> str:
    # Flip to "auto" once a client-side decoder is deployed.
    return "json"


def _normalize(fmt, fallback):
    if fmt is None:
        return fallback
    fmt = str(fmt).lower()
    return fmt if fmt in _FORMATS else fallback


# ---------------------------------------------------------------------------
# Pure-Python #MUNCH/1 encoder (fallback when pygrap.wire is not yet built)
# ---------------------------------------------------------------------------

def _encode_tables_python(*, tool, enc_id, scalars, tables):
    """Produce a #MUNCH/1 string from scalars + tables.

    Interns repeated string values in columns whose index appears in the
    intern set, emitting `@N=value` legend lines at the top.

    Format recap:
        #MUNCH/1 tool=<tool> enc=<enc_id>

        @1=<interned-value>
        ...

        scalar1=v1 scalar2=v2 ...
        __stypes=...
        __tables=<tag>:<name>:<col1>|<col2>|...

        <tag>,<cell0>,<cell1>,...
    """
    legend: dict[str, str] = {}  # value -> @N symbol
    legend_order: list[str] = []  # values in insertion order

    def intern_val(v):
        s = str(v) if v is not None else ""
        if s not in legend:
            idx = len(legend_order) + 1
            sym = f"@{idx}"
            legend[s] = sym
            legend_order.append(s)
        return legend[s]

    # Collect table specs and encoded rows
    table_specs = []  # (tag, name, cols, encoded_rows)
    for tag, name, cols, intern_indices, rows in tables:
        intern_set = set(intern_indices)
        encoded_rows = []
        for row in rows:
            cells = []
            for ci, cell in enumerate(row):
                if ci in intern_set:
                    cells.append(intern_val(cell))
                else:
                    cells.append("" if cell is None else str(cell))
            encoded_rows.append(cells)
        table_specs.append((tag, name, cols, encoded_rows))

    lines = []
    # Header
    lines.append(f"#MUNCH/1 tool={tool} enc={enc_id}")
    lines.append("")

    # Legend
    for v in legend_order:
        lines.append(f"{legend[v]}={v}")
    if legend_order:
        lines.append("")

    # Scalars
    if scalars:
        scalar_line = " ".join(f"{k}={v}" for k, v in scalars)
        lines.append(scalar_line)

    # Table schema declaration
    if table_specs:
        schema_parts = []
        for tag, name, cols, _ in table_specs:
            schema_parts.append(f"{tag}:{name}:{chr(124).join(cols)}")
        lines.append(f"__tables={chr(32).join(schema_parts)}")
        lines.append("")

        # Table rows
        for tag, name, cols, encoded_rows in table_specs:
            for row in encoded_rows:
                lines.append(f"{tag},{chr(44).join(row)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def encode_response(tool_name, response, requested_format=None, repo=None):
    """Return (payload, meta).

    payload is the original dict for json/unknown-tool, or the #MUNCH string
    for compact. meta always carries 'format' and 'tokens_saved'.
    """
    fmt = _normalize(requested_format, default_format(repo))
    schema = SCHEMAS.get(tool_name)
    if fmt == "json" or schema is None:
        return response, {"format": "json", "tokens_saved": 0}

    # Try the Rust-backed encoder first; fall back to the pure-Python one.
    _encoder = None
    try:
        import pygrap
        _encoder = pygrap.wire.encode_tables
    except AttributeError:
        pass  # pygrap.wire not yet available; use Python fallback

    if _encoder is None:
        _encoder = _encode_tables_python

    raw_bytes = len(json.dumps(response, separators=(",", ":")))
    scalars = [(k, response[k]) for k in schema["scalars"] if k in response]
    tables = []
    for tag, key, cols, intern in schema["tables"]:
        rows = [[row.get(c) for c in cols] for row in (response.get(key) or [])]
        tables.append((tag, key, cols, intern, rows))

    payload = _encoder(
        tool=tool_name, enc_id=schema["enc_id"], scalars=scalars, tables=tables,
    )
    tokens_saved = max(0, (raw_bytes - len(payload)) // _BYTES_PER_TOKEN)
    return payload, {"format": "compact", "tokens_saved": tokens_saved}
