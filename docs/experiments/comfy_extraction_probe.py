"""Run actual ComfyUI safetensors via its installed Python, without a UI server.

Pass ComfyUI root, model path, family (qwen/gemma), input JSON and output JSON.
Input supplies a system prompt and labeled cases. Scoring uses the normal project
Python separately, so this probe does not install grap in ComfyUI's environment.
"""
import json
from pathlib import Path
import sys
import time

root, model, family, input_path, output_path = sys.argv[1:]
sys.argv = [sys.argv[0]]  # ComfyUI's module imports parse command-line options
sys.path.insert(0, root)
import comfy.sd
import torch

source = json.loads(Path(input_path).read_text(encoding="utf-8"))
started = time.perf_counter()
clip = comfy.sd.load_clip([model])
report = {"model_path": model, "runtime": "ComfyUI", "family": family,
          "load_seconds": round(time.perf_counter() - started, 3), "rows": []}


def generate(system, user, limit):
    if family == "qwen":
        prompt = f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    else:
        prompt = f"<|turn>system\n{system}<turn|>\n<|turn>user\n{user}<turn|>\n<|turn>model\n"
    tokens = clip.tokenize(prompt, skip_template=True, min_length=1, thinking=False)
    with torch.inference_mode():
        ids = clip.generate(tokens, do_sample=False, max_length=limit, seed=0)
    return clip.decode(ids)


started = time.perf_counter()
report["warmup_reply"] = generate("Return ONLY JSON.", 'Return {"ready":true}.', 32)
report["warmup_seconds"] = round(time.perf_counter() - started, 3)
for case in source["cases"]:
    started = time.perf_counter()
    raw = generate(source["system"], "Extract the ontology fragment from the following text:\n\n" + case["text"], 512)
    row = {"case": case["id"], "raw": raw, "seconds": round(time.perf_counter() - started, 3)}
    report["rows"].append(row)
    Path(output_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(case["id"], row["seconds"], flush=True)
