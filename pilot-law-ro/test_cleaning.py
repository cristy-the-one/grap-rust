"""Does collapsing layout runs help or hurt extraction on TABLE-HEAVY chunks?

Gates the decision in docs/handover-gap-instability.md item #4. Same chunk, two
arms, same prompt/model/parser:
  RAW    - text as the pipeline sees it today
  CLEAN  - box-drawing dropped, dot leaders collapsed, column padding collapsed

The worry is specific: collapsing runs of spaces removes column alignment, so
adjacent table cells run together and the model may fuse or lose them. Measured
on the biggest-saving files, which are exactly the table-heavy ones.
"""
import json, os, re, sys, io, time
from pathlib import Path
P = Path(__file__).resolve().parent; R = P.parent
for k, v in {"GRAP_LLM_BASE_URL":"http://127.0.0.1:8080/v1",
             "GRAP_LLM_MODEL":"ternary-bonsai-27b",
             "GRAP_LLM_EXTRA_BODY":'{"chat_template_kwargs": {"enable_thinking": false}}',
             "GRAP_LLM_TIMEOUT":"420","GRAP_LLM_MAX_TOKENS":"2048",
             "GRAP_LLM_RETRIES":"2"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, str(R/"mcp-server"))
from grap_mcp.ingest import _text_and_label_from_json, chunk_text
from grap_mcp.kee import extract as ex
from grap_mcp.kee.llm import get_provider
sys.stdout = io.TextIOWrapper(open(1,"wb",closefd=False), encoding="utf-8",
                              errors="replace", line_buffering=True)
OUT = P/"distillation"/"cleaning-test"; OUT.mkdir(parents=True, exist_ok=True)

BOX    = re.compile(r"[─═│║┌┐└┘├┤┬┴┼╔╗╚╝╠╣╦╩╬]{2,}")
LEADER = re.compile(r"([.\-_·…—–])\1{2,}")
SPACE  = re.compile(r"[ \t\xa0]{2,}")
BLANK  = re.compile(r"\n{3,}")
def clean(t):
    t = BOX.sub(" ", t); t = LEADER.sub(r"\1", t); t = SPACE.sub(" ", t)
    return BLANK.sub("\n\n", t)

CANDIDATES = ["18425.json","18903.json","50956.json","18338.json","17126.json"]
prov = get_provider()
rows = []
for name in CANDIDATES:
    f = R/"real-data-law-ro"/name
    t,_ = _text_and_label_from_json(json.loads(f.read_text(encoding="utf-8")), f.stem)
    chunks = chunk_text(t, 2000)
    # pick the chunk with the most layout noise (most collapsible chars)
    scored = sorted(((len(c)-len(clean(c)), i, c) for i,c in enumerate(chunks)),
                    reverse=True)
    saving, idx, raw = scored[0]
    cl = clean(raw)
    for arm, text in (("RAW", raw), ("CLEAN", cl)):
        t0=time.time()
        try:
            out = prov.complete(ex.SYSTEM_PROMPT, ex.build_user_prompt(text)); err=None
        except Exception as e:
            out, err = "", str(e)[:80]
        obj = ex.extract_json(out) if out else None
        frag = ex.normalize_fragment(obj, f"{f.stem}#{idx}") if obj else {"outcomes":[],"relations":[]}
        chars = sum(len(o.get("characteristics") or []) for o in frag["outcomes"])
        rows.append({"file":name,"chunk":idx,"arm":arm,"chars_in":len(text),
                     "parsed":obj is not None,"outcomes":len(frag["outcomes"]),
                     "characteristics":chars,"relations":len(frag["relations"]),
                     "secs":round(time.time()-t0,1),"error":err})
        (OUT/f"{f.stem}_{idx}_{arm}.txt").write_text(
            f"=== INPUT ({len(text)} chars) ===\n{text}\n\n=== RESPONSE ===\n{out}",
            encoding="utf-8")
        print(f"  {name:<13} #{idx} {arm:<5} in={len(text):>5} outc={len(frag['outcomes']):>2} "
              f"chars={chars:>3} rel={len(frag['relations']):>2} "
              f"{'PARSEFAIL' if obj is None else ''} {rows[-1]['secs']}s", flush=True)
    print(f"     (layout saving on this chunk: {saving} chars)", flush=True)

json.dump(rows, open(OUT/"result.json","w",encoding="utf-8"), indent=2, ensure_ascii=False)
def agg(arm):
    rs=[r for r in rows if r["arm"]==arm]; n=len(rs) or 1
    return (sum(r["outcomes"] for r in rs)/n, sum(r["characteristics"] for r in rs)/n,
            sum(r["relations"] for r in rs)/n, sum(0 if r["parsed"] else 1 for r in rs))
print("\n=== AGGREGATE (outcomes / characteristics / relations / parse-fails) ===")
for arm in ("RAW","CLEAN"):
    o,c,rel,pf = agg(arm); print(f"  {arm:<5}: {o:.1f} / {c:.1f} / {rel:.1f} / {pf}")
ro,rc,rr,_ = agg("RAW"); co,cc,cr,_ = agg("CLEAN")
print(f"\n  CLEAN vs RAW: outcomes {co/max(ro,.01):.2f}x  characteristics {cc/max(rc,.01):.2f}x  relations {cr/max(rr,.01):.2f}x")
print(f"  artifacts: {OUT}")
