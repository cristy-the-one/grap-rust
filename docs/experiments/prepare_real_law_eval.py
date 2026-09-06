"""Freeze source excerpts and a cheap whole-corpus audit before model evaluation.

Labels are source-reviewed engineering checks, not independent legal annotation.
Run from the repository root. Original documents are never modified.
"""
import collections
import hashlib
import json
from pathlib import Path

from grap_mcp.kee.pipeline import read_chunks

ROOT = Path("real-data-law-ro")
OUT = Path("docs/experiments")


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    rows, errors = [], []
    for path in sorted(ROOT.glob("*.json")):
        try:
            raw = path.read_bytes()
            doc = json.loads(raw.decode("utf-8-sig"))
            body = doc["text_complet"]
            rows.append({"file": path.name, "sha256": hashlib.sha256(raw).hexdigest(),
                         "type": doc["tip_act"], "chars": len(body), "bytes": len(raw),
                         "placeholder": body.strip() in ("Act în curs de procesare.", "Act in curs de procesare."),
                         "repeated_title": body[:50].lower().strip() in doc["titlu"].lower()})
        except Exception as exc:
            errors.append({"file": path.name, "error": str(exc)})
    lengths = sorted(r["chars"] for r in rows)
    chunk_counts = {}
    for size in (800, 1600, 2400):
        stats = {}
        chunks = read_chunks(str(ROOT), chunk_chars=size, stats=stats)
        chunk_counts[str(size)] = {"llm_chunks": len(chunks), "tabular_skipped": stats["tabular_skipped"]}
    save("real-law-corpus-audit-2026-09.json", {
        "files": len(rows), "errors": errors, "bytes": sum(r["bytes"] for r in rows),
        "body_chars": sum(lengths), "placeholders": sum(r["placeholder"] for r in rows),
        "types": dict(collections.Counter(r["type"] for r in rows)),
        "body_char_quantiles": {str(p): lengths[int((len(lengths)-1)*p)] for p in (0,.25,.5,.75,.9,.95,.99,1)},
        "chunk_counts": chunk_counts, "documents": rows})

    cases = []

    def add(key, docid, start=None, end=None, **labels):
        path = ROOT / (docid + ".json")
        raw = path.read_bytes(); doc = json.loads(raw.decode("utf-8-sig")); body = doc["text_complet"]
        lo = body.index(start) if start else 0
        hi = body.index(end, lo + 1) if end else len(body)
        passage = body[lo:hi]
        lo += len(passage) - len(passage.lstrip())
        hi = lo + len(passage.strip())
        cases.append({"id": key, "text": body[lo:hi], "source_file": path.as_posix(),
                      "source_sha256": hashlib.sha256(raw).hexdigest(), "field": "text_complet",
                      "start": lo, "end": hi, "source_url": doc["link"], **labels})

    # Expectations written from the source before asking an evaluated model.
    add("office-subordination", "16678", "+ Articolul 1", "+ Articolul 2",
        relations=[["Oficiul Național al Burselor de Studii în Străinătate", "subordonat", "Ministerului Educației Naționale"]])
    add("bank-register-fields", "16694", "+ Articolul 2", "+ Articolul 3",
        requirements=[["Registrul bancar", "denumirea"], ["Registrul bancar", "adresa"]])
    add("shareholder-payment", "17754", "+ Articolul 2", "+ Articolul 3",
        requirements=[["acţionar", "integral"]])
    add("bank-capital-deadline", "17754", "+ Articolul 4", "+ Articolul 5",
        requirements=[["Băncile", "30 aprilie 2000"]])
    add("agent-prohibitions", "17984", "+ Articolul 1", "+ Articolul 3",
        requirements=[["agentul de valori mobiliare", "personal"], ["S.V.M.", "verificarea"]])
    add("exception-preservation", "17984", "+ Articolul 4", "+ Articolul 5",
        requirements=[["Ordinele de vânzare prin procura autentică", "excepţionale"],
                      ["Ordinele de vânzare prin procura autentică", "acordul"]])
    add("explicit-abbreviations", "18185", "+ Articolul 2", ". manipularea pieţei",
        required_entities=["C.N.V.M.", "Comisia Naţionala a Valorilor Mobiliare", "OSP",
                           "Oficiul de supraveghere a pieţelor reglementate de valori mobiliare"],
        no_requirements=["C.N.V.M.", "OSP"])
    add("confidential-information", "18185", "+ Articolul 7", "-----------",
        review_checks=["Preserve strict secrecy and restricted access; do not convert them into unconditional access."])
    add("broadcast-exclusions", "16686", "+ Articolul 1", "+ Articolul 2",
        requirements=[["Posturile de televiziune", "40%"], ["Posturile de televiziune", "10%"]],
        review_checks=["The majority calculation excludes news, sports, games, advertising and teletext.",
                       "40% refers to the European content already broadcast, not all airtime."])
    add("banking-act-identities", "17994", required_entities=["Legea nr. 101/1998", "Legea bancară nr. 58/1998", "Normele nr. 2/1999"])
    add("nested-amendment-identities", "16955", required_entities=["Legea nr. 2/1992", "Legea nr. 17/1999", "Legea nr. 154/1998"],
        review_checks=["Keep Law 2/1992, Law 17/1999 and Law 154/1998 distinct.",
                       "Do not flatten historical amendment/approval chains into the current act's actions."])
    add("missing-body", "109203", empty=True)
    save("real-law-extraction-cases.json", cases)

    pairs = []
    def pair(a, b, same, docid, why):
        pairs.append({"a": a, "b": b, "same": same, "source_file": f"real-data-law-ro/{docid}.json", "reason": why})
    for a,b,doc,why in [
        ("Banca Naţionala a României", "Băncii Naţionale a României", "16694", "Nominative/genitive of the same bank"),
        ("C.N.V.M.", "Comisia Naţionala a Valorilor Mobiliare", "18185", "Explicit abbreviation definition"),
        ("OSP", "Oficiul de supraveghere a pieţelor reglementate de valori mobiliare", "18185", "Explicit abbreviation definition"),
        ("S.V.M.", "societate de valori mobiliare", "17984", "Explicit abbreviation definition"),
        ("Legea bancară nr. 58/1998", "Legea nr. 58/1998", "16694", "Same act number and year; omitted descriptive title"),
        ("Normelor nr. 2/1999", "Normele nr. 2/1999", "17994", "Inflection of same act reference"),
        ("Ministerului Educației Naționale", "Ministerul Educației Naționale", "16678", "Genitive/nominative of same ministry"),
        ("Oficiul Național al Burselor de Studii în Străinătate", "Oficiului Național al Burselor de Studii în Străinătate", "16678", "Inflection of same office"),
    ]: pair(a,b,True,doc,why)
    for a,b,doc,why in [
        ("Legea nr. 101/1998", "Legea nr. 58/1998", "17994", "Different law numbers"),
        ("Legea nr. 2/1992", "Legea nr. 17/1999", "16955", "Different acts in the amendment chain"),
        ("Legea nr. 17/1999", "Legea nr. 154/1998", "16955", "Different acts"),
        ("Normele Băncii Naţionale a României nr. 3/1999", "Normele Băncii Naţionale a României nr. 4/1998", "17754", "Different numbered norms"),
        ("Normele nr. 2/1999", "Legea nr. 101/1998", "17994", "Different act kinds and identifiers"),
        ("Banca Comercială Română - S.A.", "Banca Română de Comerţ Exterior - S.A.", "16694", "Distinct banks in the source footnote; names inflected to nominative"),
        ("Băncii Agricole - S.A.", "Băncii Române pentru Dezvoltare - S.A.", "16694", "Distinct named banks"),
        ("registrul bancar", "registrul comerţului", "16694", "Distinct registers"),
        ("Ministerului Educației Naționale", "ministrului educației naționale", "16678", "Ministry institution versus minister office/person"),
        ("Consiliului Europei", "Consiliul Naţional al Audiovizualului", "16686", "Distinct councils"),
        ("riscul creditului", "riscul de piaţa", "18185", "Distinct risk categories explicitly defined"),
        ("Bursa de Valori Bucureşti", "Piaţa RASDAQ", "18185", "Distinct markets"),
    ]: pair(a,b,False,doc,why)
    save("real-law-entity-pairs.json", pairs)
    print(json.dumps({"documents":len(rows), "chunks":chunk_counts, "cases":len(cases), "pairs":len(pairs)}))


if __name__ == "__main__":
    main()
