"""Record the code and local runtime behind a result, not just model settings."""
import hashlib
from pathlib import Path
import platform
import urllib.request
import json

ROOT = Path(__file__).resolve().parents[2]


def provenance():
    files = sorted((ROOT / 'mcp-server/grap_mcp/kee').glob('*.py'))
    files += [Path(__file__), ROOT / 'docs/experiments/profile_llm_ingestion.py',
              ROOT / 'docs/experiments/evaluate_gemma_runtime.py',
              ROOT / 'docs/experiments/evaluate_selective_thinking.py']
    hashes = {str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in files}
    result = {'python': platform.python_version(), 'code_sha256': hashes,
              'code_fingerprint': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}
    for endpoint in ('version', 'tags'):
        with urllib.request.urlopen('http://127.0.0.1:11434/api/' + endpoint, timeout=10) as response:
            body = json.load(response)
        result[endpoint] = body if endpoint == 'version' else [
            {'name': m['name'], 'digest': m['digest']} for m in body.get('models', [])]
    return result
