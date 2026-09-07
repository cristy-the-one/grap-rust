"""Freeze assistant-authored reference cases from source text, before model trials."""
import hashlib
import json
from pathlib import Path


# Source selections and reference subjects are manually specified, not derived
# from extractor output. Holdout documents are disjoint from development documents.
SPECS = [
    ("dev", "19961", "Executorii bancari, precum", "îi eliberează de aceasta obligaţie.", "obligation",
     ["Executorii bancari, precum şi oricare alt personal angajat în compartimentul acestora"],
     ["chiar şi după încetarea funcţiei", "cu excepţia cazurilor"]),
    ("dev", "19961", "Este interzis executorilor", "gradul al patrulea inclusiv.", "prohibition",
     ["executorilor bancari"], ["direct sau prin interpusi", "pentru ei sau pentru alţii"]),
    ("dev", "19961", "Dosarele de executare aflate", "preşedintelui instanţei de executare.", "permission",
     ["Dosarele de executare aflate în curs de rezolvare la data intrării în vigoare a prezentului statut"],
     ["pe baza solicitării scrise"]),
    ("dev", "19961", "Persoanele interesate în efectuarea", "depuse la dosarul executării.", "right",
     ["Persoanele interesate în efectuarea executării, care nu înţeleg sau nu vorbesc limba română"],
     ["printr-un interpret sau traducător"]),
    ("dev", "19961", "Toate actele întocmite", "se redactează în limba română.", "obligation",
     ["Toate actele întocmite de executorul bancar"], ["în limba română"]),
    ("dev", "16861", "Direcţia generală de prognoza se organizează", "care se desfiinţează.", "fact",
     ["Direcţia generală de prognoza"], ["în subordinea Ministerului Finanţelor"]),
    ("dev", "18860", "Agenţia Naţionala pentru Locuinţe,", "autonomie financiară.", "fact",
     ["Agenţia Naţionala pentru Locuinţe", "Agenţia Naţionala pentru Locuinţe, denumita în continuare A.N.L."],
     ["personalitate juridică", "autonomie financiară"]),
    ("dev", "18860", "Membrii Consiliului naţional de coordonare", "directorului general al A.N.L.", "right",
     ["Membrii Consiliului naţional de coordonare al A.N.L."], ["nu sunt salariaţi", "25%", "de şedinţa"]),
    ("dev", "18860", "Membrii Consiliului de administraţie al A.N.L., cu excepţia directorului general al A.N.L., nu sunt salariaţi", "directorului general al A.N.L.", "right",
     ["Membrii Consiliului de administraţie al A.N.L.", "Membrii Consiliului de administraţie al A.N.L., cu excepţia directorului general al A.N.L."],
     ["cu excepţia directorului general", "20%", "lunară"]),
    ("dev", "83906", "Pentru asigurarea efectuării transportului", "normele tehnice specifice în vigoare.", "obligation",
     ["vehiculele feroviare de orice tip aparţinând agentilor economici"], ["în condiţii de siguranţă a circulaţiei"]),
    ("dev", "83906", "Prezentele norme nu se aplică", "infrastructurii feroviare publice.", "fact",
     ["Prezentele norme"], ["nu se aplică"]),
    ("holdout", "18660", "Bursa de Valori Bucureşti, Piaţa RASDAQ, camerele arbitrale", "atribuţiilor ce le revin.", "obligation",
     ["Bursa de Valori Bucureşti, Piaţa RASDAQ, camerele arbitrale şi tribunalul arbitral"],
     ["sa nu publice", "sa nu divulge", "interesele părţilor"]),
    ("holdout", "17438", "Este interzisă înstrăinarea tractoarelor", "achitarea integrală a creditului.", "prohibition",
     ["înstrăinarea tractoarelor, combinelor, maşinilor şi utilajelor agricole, precum şi a instalaţiilor pentru irigat, achiziţionate în condiţiile ordonanţei şi ale prezentelor norme metodologice"],
     ["înainte de achitarea integrală a creditului"]),
    ("holdout", "17438", "În cazul înstrăinării sunt aplicabile", "art. 6 din ordonanţa.", "fact",
     ["sancţiunile civile prevăzute la art. 6 din ordonanţa"], ["În cazul înstrăinării"]),
    ("holdout", "17357", "Se interzice conectarea la reţelele furnizorului", "cu echipamente de măsurare.", "prohibition",
     ["conectarea la reţelele furnizorului a instalaţiei de utilizare a unui consumator"],
     ["dacă nu este prevăzută cu echipamente de măsurare"]),
    ("holdout", "19161", "Sumele ce urmează a fi restituite", "după caz.", "fact",
     ["Sumele ce urmează a fi restituite beneficiarilor în condițiile art. III alin. (1)"],
     ["nu se actualizează", "nu sunt purtătoare de dobânzi"]),
    ("holdout", "20030", "Pentru a sprijini actul decizional", "documentele și informațiile solicitate.", "obligation",
     ["toate societățile comerciale, companiile și societățile naționale, regiile autonome din subordinea F.P.S. sau a ministerelor, precum și instituțiile publice și agențiile subordonate Guvernului"],
     ["imediat", "structurilor locale și centrale ale RICOP"]),
]


def main():
    root = Path(__file__).resolve().parents[2]
    out = root / "docs/experiments/real-law-semantic-cases.json"
    if out.exists():
        raise SystemExit("Frozen annotations already exist; do not overwrite them.")
    cases = []
    for index, (split, source, prefix, suffix, kind, subjects, qualifiers) in enumerate(SPECS):
        path = root / "real-data-law-ro" / f"{source}.json"
        raw = path.read_bytes()
        body = json.loads(raw.decode("utf-8-sig"))["text_complet"]
        start = body.index(prefix)
        end = body.index(suffix, start + len(prefix)) + len(suffix)
        passage = body[start:end]
        assert all(subject in passage for subject in subjects), (source, subjects)
        assert all(q in passage for q in qualifiers), (index + 1, [q for q in qualifiers if q not in passage])
        cases.append({"id": f"semantic-{index+1:02}", "split": split,
            "source_file": path.relative_to(root).as_posix(), "source_sha256": hashlib.sha256(raw).hexdigest(),
            "start": start, "end": end, "text": passage, "expected_kind": kind,
            "accepted_subjects": subjects, "required_text": qualifiers})
    dev = {c["source_file"] for c in cases if c["split"] == "dev"}
    assert not dev.intersection(c["source_file"] for c in cases if c["split"] == "holdout")
    out.write_text(json.dumps({"annotation": "Assistant-authored before model outputs; not expert legal validation.",
        "rubric": "Primary operative kind and complete governed subject, which may be an action/object in passive wording. Qualifications must remain in the same statement. Fact includes exclusions and legal consequences; it does not assert current validity.",
        "cases": cases}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
