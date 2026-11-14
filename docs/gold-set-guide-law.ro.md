# Ghid de etichetare — setul de referință pentru legislația română

**Pentru:** revizorul cu pregătire juridică. Nu sunt necesare cunoștințe de programare.
**Veți primi:** trei foi de calcul și textele-sursă la care se referă.
**Efort estimat:** aproximativ 20 de ore în total. Puteți lucra în orice ordine și în oricâte
reprize doriți.

> **De ce unele cuvinte rămân în engleză.** Foile sunt citite de un program. Numele coloanelor și
> valorile din coloanele `kind`, `label` și `type` trebuie scrise **exact** ca în acest ghid, în
> engleză. Tot restul — subiectul, condițiile, întrebările, răspunsurile, observațiile — se scrie
> în română, așa cum apare în text sau cum vă exprimați în mod firesc.

## De ce există acest set

Avem un program care citește texte juridice românești și notează ce crede că spun ele. Nu știm
cât de des are dreptate. Răspunsurile dumneavoastră devin referința față de care este evaluat,
așa că trebuie să fie **ale dumneavoastră**: ceea ce conchide un cititor cu pregătire juridică din
text, fără să știe ce a produs programul.

## Patru reguli mai importante decât toate celelalte

1. **Lucrați numai pe baza textului.** Judecați ce afirmă pasajul. Nu folosiți ceea ce știți
   despre faptul că dispoziția mai este sau nu în vigoare, că a fost modificată ulterior ori despre
   felul în care o aplică instanțele.
2. **Nu folosiți un asistent de inteligență artificială** pentru etichetare și nu introduceți
   pasajele într-unul.
3. **„Neclar” este un răspuns valabil.** Dacă un jurist competent ar putea citi pasajul în două
   feluri, spuneți acest lucru și explicați într-un rând de ce. Un răspuns forțat este mai rău
   decât un „neclar” onest.
4. **Nu ocoliți cazurile incomode.** Etichetați fiecare rând primit. Dacă un pasaj este defect
   (tăiat la mijlocul frazei, ilizibil), marcați-l `broken` și treceți mai departe — nu îl
   înlocuiți cu unul mai convenabil.

Vă rugăm să trimiteți fișierele completate numai proprietarului proiectului. O parte din munca
dumneavoastră este păstrată ca „examen sigilat”, pe care dezvoltatorii nu îl văd niciodată; nu
este nevoie să știți care parte.

---

## Foaia 1 — Enunțuri (`statements.csv`, aproximativ 150 de pasaje)

Fiecare rând arată un pasaj și articolul din care face parte. Un pasaj poate conține **o regulă,
mai multe sau niciuna**. Adăugați câte un rând pentru fiecare regulă găsită (copiați valorile din
`packet_id`, `source_id`, `item_id` și `passage`). Dacă nu există nicio regulă, completați un
singur rând cu `kind` = `other`.

### Coloanele pe care le completați

| Coloană | Ce scrieți |
|---|---|
| `kind` | Unul dintre cele opt tipuri de mai jos sau `unclear`. |
| `subject` | Cine sau ce este vizat de regulă — **copiat exact** din pasaj, cu toate cuvintele și cu diacriticele așa cum apar. Lăsați gol dacă pasajul nu îl indică. |
| `conditions` | Cuvintele care limitează *când* se aplică regula („în cazul în care…”, „numai dacă…”), copiate exact. Gol dacă nu există. |
| `exceptions` | Cuvintele care exclud ceva („cu excepția…”), copiate exact. Gol dacă nu există. |
| `dates_and_amounts` | Orice termen, dată, prag sau procent de care depinde regula, copiat exact. |
| `acts_referred_to` | Fiecare act menționat în pasaj, așa cum este scris („Legea nr. 58/1998”). |
| `sure` | `yes`, `fairly` sau `no` — cât de sigur(ă) sunteți. |
| `note` | Orice ar trebui să știm — mai ales dacă niciun tip nu se potrivește bine. |

### Cele opt tipuri

| `kind` | Îl folosiți când pasajul… | Formulări tipice |
|---|---|---|
| `obligation` | impune o îndatorire sau spune ce trebuie să conțină ori să atingă ceva | „este obligat”, „trebuie”, „va cuprinde”, „vor asigura” |
| `prohibition` | interzice o conduită — chiar dacă numai în anumite condiții | „se interzice”, „este interzis”, „nu pot” |
| `permission` | îngăduie o conduită fără a o impune | „poate”, „pot”, „are dreptul să opteze” |
| `right` | conferă un drept unui beneficiar | „are dreptul la”, „beneficiază de”, „primește” |
| `definition` | atribuie un înțeles unui termen | „prin … se înțelege”, „în sensul prezentei legi” |
| `amendment` | modifică, abrogă sau înlocuiește un alt text | „se modifică”, „se abrogă”, „se completează”, „va avea următorul cuprins” |
| `fact` | descrie sau stabilește ceva fără a impune cuiva o conduită: se înființează o instituție, se stabilește o subordonare, se precizează un domeniu de aplicare | „se înființează”, „funcționează în subordinea”, „se aplică” |
| `other` | nu afirmă nimic: un titlu, un bloc de semnături, o simplă trimitere | — |

### Reguli pentru cazurile dificile

- **Subiectul este cine sau ce este vizat, nu când sau unde.** În „La înființarea unei bănci,
  acționarii vor vărsa…”, subiectul este *acționarii*, nu înființarea băncii.
- **Reguli la diateza pasivă:** nu inventați persoana responsabilă. Dacă textul spune că
  vărsămintele „se efectuează”, subiectul este *vărsămintele*.
- **Copiați întregul subiect, cu determinările lui.** „băncile autorizate și băncile care au
  aprobarea de constituire” este un singur subiect, nu doar „băncile”.
- **O interdicție condiționată rămâne interdicție.** Treceți condiția în coloana ei; nu schimbați
  tipul.
- **Primirea unui beneficiu este un drept, nu o obligație** a celui care îl primește.
- **„nu este obligat”** nu este o obligație. Stabiliți dacă este o permisiune (`permission`) sau un
  fapt (`fact`).
- **A cita un act nu înseamnă a-l modifica.** `amendment` cere cuvinte care exprimă explicit o
  modificare.
- **O listă de elemente obligatorii** („registrul va cuprinde: a)… b)… c)…”) este **o singură**
  obligație; păstrați lista întreagă, nu o împărțiți pe litere.
- **Două reguli într-o singură frază**, legate prin „iar” sau prin punct și virgulă: două rânduri.

### Exemple rezolvate

> *Se interzice amplasarea de unităţi cu profil alimentar în spaţiile de locuit.*

`kind` = `prohibition` · `subject` = *amplasarea de unităţi cu profil alimentar în spaţiile de
locuit* · `conditions` — · `exceptions` —

> *Procedura de negociere directa este aplicabilă în cazul în care licitaţia publică nu a condus
> la desemnarea unui câştigător.*

`kind` = `fact` · `subject` = *Procedura de negociere directa* · `conditions` = *în cazul în care
licitaţia publică nu a condus la desemnarea unui câştigător*

> *Prevederile prezentei legi nu se aplică contractelor de proiectare, finanţare, construcţie…
> care se încheie potrivit procedurilor prevăzute în Ordonanţa Guvernului nr. 16/2002, aprobată
> cu modificări şi completări prin Legea nr. 470/2002…*

`kind` = `fact` (precizează domeniul de aplicare) · `subject` = *Prevederile prezentei legi* ·
`acts_referred_to` = *Ordonanţa Guvernului nr. 16/2002; Legea nr. 470/2002* — și **nu**
`amendment`: pasajul amintește că un act a fost aprobat cu modificări, dar el însuși nu modifică
nimic.

**Observație despre copiere.** Copiați textul exact cum apare în pasaj, inclusiv diacriticele în
forma în care sunt scrise acolo (de exemplu „ţ” sau „ț”). Nu le corectați.

---

## Foaia 2 — Același lucru sau nu? (`pairs.csv`, aproximativ 200 de perechi)

Fiecare rând arată două denumiri, fiecare cu propoziția din care provine și cu documentul ei.
Hotărâți dacă se referă la **același lucru din realitate** și scrieți răspunsul în coloana `label`.

| `label` | Înțeles |
|---|---|
| `same` | Unul și același act, instituție, funcție, persoană sau concept. Diferențele de scriere, de caz gramatical ori abrevierea nu contează. |
| `different` | Două lucruri distincte, oricât de asemănătoare ar fi denumirile. |
| `same only here` | O abreviere sau o denumire scurtă care înseamnă acest lucru **în acest document**, pentru că documentul o definește, dar care în altă parte ar putea însemna altceva. |
| `unclear` | Propozițiile afișate nu sunt suficiente pentru a decide. Spuneți în `note` ce lipsește. |

Îndrumări:

- **Ministerul și ministrul sunt lucruri diferite.** O instituție și conducătorul ei sunt două
  entități.
- **Actele sunt aceleași numai dacă tipul, numărul și anul coincid** — și, unde este cazul,
  emitentul. „Legea nr. 58/1998” și „Legea nr. 101/1998” sunt diferite, oricât de apropiată ar fi
  formularea.
- **„Guvernul”, „Guvernul României”, „GUVERNUL”** — `same`.
- **„prezenta lege”, „prezenta hotărâre”** — același lucru cu actul din documentul în care apar
  și cu nimic altceva. Dacă cele două rânduri provin din documente diferite, sunt `different`.
- **Un act și unul dintre articolele sale** sunt `different`.
- **O instituție redenumită în timp**: `unclear`, în afară de cazul în care textul însuși spune
  că este succesoarea celeilalte.

---

## Foaia 3 — Întrebări (`questions.csv`, aproximativ 100)

Aici **dumneavoastră scrieți** întrebările. Fiecare trebuie să fie ceva ce ar întreba cu adevărat
un practician și trebuie să poată primi răspuns — sau, în mod deliberat, să nu poată primi —
numai din documentele pe care le-ați primit.

| Coloană | Ce scrieți |
|---|---|
| `question` | În română, așa cum ați întreba în mod firesc. |
| `type` | Unul dintre cele cinci tipuri de mai jos, exact cum este scris. |
| `answer` | Cel mult două-trei propoziții. Pentru tipul `not answerable` scrieți exact `not in the documents`. |
| `where` | Identificatorul documentului și articolul (și alineatul) pentru **fiecare** pasaj necesar răspunsului, despărțite prin punct și virgulă. Nu lăsați gol, cu excepția întrebărilor `not answerable`. |
| `source_id` | Identificatorul documentului principal la care se referă întrebarea. |
| `note` | Orice subtilitate: o capcană, o excepție care schimbă răspunsul. |

Vă rugăm să repartizați întrebările aproximativ astfel:

| `type` | Număr | Cum arată |
|---|---:|---|
| `direct` — răspunde o singură dispoziție | 30 | „Ce termen are… pentru…?” |
| `conditional` — răspunsul depinde de o condiție sau de o excepție | 25 | „Poate… dacă…?” |
| `combined` — sunt necesare două sau mai multe dispoziții, eventual din acte diferite | 20 | „Cine aprobă… și în ce termen se publică?” |
| `relation between acts` — care act îl modifică, îl abrogă, îl aprobă sau îl pune în aplicare pe altul | 10 | „Ce act a modificat articolul… din…?” |
| `not answerable` — sună rezonabil, dar documentele primite nu conțin răspunsul | 15 | la `answer` scrieți `not in the documents` |

Îndrumări:

- Nu reluați în întrebare formularea dispoziției; întrebați așa cum ar întreba un client.
- Evitați întrebările al căror răspuns este un simplu „da” sau „nu”, fără o dispoziție la care să
  trimiteți.
- Întrebările `not answerable` contează la fel de mult ca celelalte: ele verifică dacă programul
  recunoaște că nu știe.

---

## La final

Trimiteți cele trei fișiere proprietarului proiectului. Dacă, după o săptămână, vă puteți face
timp o oră, veți primi din nou aproximativ cincisprezece rânduri din Foaia 1, fără răspunsurile
dumneavoastră. Reetichetarea lor ne arată cât de mult se contrazice pe sine un specialist atent —
ceea ce stabilește limita a ceea ce i se poate cere, în mod rezonabil, oricărui program.
