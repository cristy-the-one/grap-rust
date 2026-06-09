"""Optional compact #MUNCH-style wire encoding for MCP tool responses.

JSON is the default; compact is opt-in per call until a client decoder ships.
The encoding itself is produced exclusively by the Rust core via
``pygrap.wire.encode_tables`` — there is intentionally no second (Python)
encoder, so the wire format has a single source of truth. If the native
binding is unavailable, we degrade to JSON rather than emit a divergent format.
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


def encode_response(tool_name, response, requested_format=None, repo=None):
    """Return ``(payload, meta)``.

    ``payload`` is the original dict for json / unknown-tool / unavailable
    binding, or the ``#MUNCH/1`` string for the compact path. ``meta`` always
    carries ``format`` and ``tokens_saved``.
    """
    fmt = _normalize(requested_format, default_format(repo))
    schema = SCHEMAS.get(tool_name)
    if fmt == "json" or schema is None:
        return response, {"format": "json", "tokens_saved": 0}

    # The canonical encoder lives in the Rust core (pygrap.wire). If the native
    # binding is unavailable, degrade to JSON — never a divergent Python format.
    try:
        import pygrap

        encode_tables = pygrap.wire.encode_tables
    except (ImportError, AttributeError):
        return response, {
            "format": "json",
            "tokens_saved": 0,
            "encoding_note": "pygrap.wire unavailable; returned JSON",
        }

    raw_bytes = len(json.dumps(response, separators=(",", ":")))
    scalars = [(k, response[k]) for k in schema["scalars"] if k in response]
    tables = []
    for tag, key, cols, intern in schema["tables"]:
        rows = [[row.get(c) for c in cols] for row in (response.get(key) or [])]
        tables.append((tag, key, cols, intern, rows))

    payload = encode_tables(
        tool=tool_name, enc_id=schema["enc_id"], scalars=scalars, tables=tables,
    )
    tokens_saved = max(0, (raw_bytes - len(payload)) // _BYTES_PER_TOKEN)
    return payload, {"format": "compact", "tokens_saved": tokens_saved}
