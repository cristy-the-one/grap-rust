"""Suite-wide test defaults."""

import os

# R-07: a missing embedder backend raises unless this is set. Existing tests
# rely on the hashing fallback, so allow the stub once for the whole suite.
os.environ["GRAP_EMBED_ALLOW_STUB"] = "1"
