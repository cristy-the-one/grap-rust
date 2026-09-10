"""Explicit, load-only warm-up for a local Ollama extraction session.

No synthetic legal replies, chat history or graph writes. This moves model load
latency before the first extraction; it does not teach the model legal concepts.
"""
import argparse
import json
import time
import urllib.request


def warm_models(model="grap-legal-gemma4", embedding_model="bge-m3", *,
                base_url="http://127.0.0.1:11434", keep_alive_seconds=600,
                timeout=180):
    if isinstance(keep_alive_seconds, bool) or not isinstance(keep_alive_seconds, int) or not 1 <= keep_alive_seconds <= 3600:
        raise ValueError("keep_alive_seconds must be an integer from 1 to 3600")
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v1"):
        raise ValueError("warm-up requires the native Ollama base URL, without /v1")
    prepared = []
    for endpoint, name, extra in (("chat", model, {}), ("embed", embedding_model, {"input": []})):
        if not name:
            continue
        body = {"model": name, "keep_alive": keep_alive_seconds, **extra}
        request = urllib.request.Request(f"{base_url}/api/{endpoint}", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
        if result.get("error"):
            raise RuntimeError(f"Model warm-up failed: {result['error']}")
        prepared.append({"model": name, "endpoint": endpoint, "seconds": time.perf_counter() - started})
    # A load response alone cannot guarantee both models fit together.
    with urllib.request.urlopen(f"{base_url}/api/ps", timeout=timeout) as response:
        resident = [row["name"] for row in json.load(response).get("models", [])]
    def canonical(name):
        return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"
    missing = [row["model"] for row in prepared if canonical(row["model"]) not in {canonical(n) for n in resident}]
    return {"prepared": prepared, "resident_models": resident, "missing_models": missing,
            "ready": not missing, "keep_alive_seconds": keep_alive_seconds,
            "scope": "Load-only readiness snapshot; later requests, expiry or memory pressure can change residency."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="grap-legal-gemma4")
    parser.add_argument("--embedding-model", default="bge-m3")
    parser.add_argument("--keep-alive-seconds", type=int, default=600)
    args = parser.parse_args()
    result = warm_models(args.model, args.embedding_model, keep_alive_seconds=args.keep_alive_seconds)
    print(json.dumps(result, indent=2))
    if not result["ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
