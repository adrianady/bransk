# Design: ierarhie teorie → sub-teorie → claim, plus stance per claim

Stare: **propus**, neimplementat. Se aprobă înainte de cod.

## 1. De ce nu tabelă nouă pentru teorie/sub-teorie

`theories` are deja `parent_id` (arbore arbitrar) și e folosită de `TheoriesView`. Adăugăm o
singură coloană discriminantă, în loc să duplicăm structura:

```sql
ALTER TABLE theories ADD COLUMN node_type TEXT DEFAULT 'theory';
-- 'theory' | 'subtheory' | 'claim'
ALTER TABLE theories ADD COLUMN statement TEXT DEFAULT '';      -- formularea testabilă a claim-ului
ALTER TABLE theories ADD COLUMN test_variable TEXT DEFAULT '';   -- opțional: X
ALTER TABLE theories ADD COLUMN test_condition TEXT DEFAULT '';  -- opțional: în condiția Z
```

Migrare aditivă, idempotentă (`try/except sqlite3.OperationalError`), ca restul schemei.
`node_type='theory'` pe rândurile existente este valoarea corectă din oficiu: azi toate sunt teorii.

Invariant: `node_type='claim'` fără `statement` este invalid (validare în router, nu CHECK, ca să nu
blocăm datele vechi).

**De ce claim-ul e frunză și nu nod de sine stătător:** stance-ul se calculează pe afirmații
testabile. Dacă un claim ar avea copii, agregarea ar deveni ambiguă (ce înseamnă „studiul susține
sub-arborele"?). Frunza e o regulă simplă, verificabilă.

## 2. Muchii de stance — tabelă nouă, nu refolosirea `theory_evidence`

`theory_evidence` are doar `pro`/`contra`, fără citat, fără stare de confirmare, fără rationale.
Rămâne neatinsă (o folosește TheoriesView). Stance-ul nou are nevoie de altceva:

```sql
CREATE TABLE claim_stance (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id          INTEGER NOT NULL REFERENCES theories(id) ON DELETE CASCADE,
    entry_id          INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    direction         TEXT NOT NULL
                      CHECK (direction IN ('supports','contradicts','mixed','neutral')),
    aspect            TEXT DEFAULT '',          -- dimensiunea vizată, dacă claim-ul e descompus
    highlight_id      INTEGER REFERENCES pdf_highlights(id) ON DELETE SET NULL,
    evidence_page     INTEGER,                  -- util când dovada vine din notă, nu din highlight
    evidence_start    INTEGER,                  -- offset în page_texts.text
    evidence_end      INTEGER,
    evidence_quote    TEXT NOT NULL,            -- citatul VERBATIM (verificat la salvare)
    reasoning         TEXT DEFAULT '',          -- 1-3 propoziții
    confidence        REAL,                     -- din logprobs / NLI, nu din text
    status            TEXT NOT NULL DEFAULT 'proposed'
                      CHECK (status IN ('proposed','accepted','rejected','insufficient')),
    proposed_by       TEXT NOT NULL DEFAULT 'ai',   -- 'ai' | 'user'
    provider          TEXT DEFAULT '',
    model             TEXT DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at        TEXT
);
CREATE INDEX idx_claim_stance_claim ON claim_stance(claim_id, status);
CREATE INDEX idx_claim_stance_entry ON claim_stance(entry_id, status);
```

### Invarianți (aplicați în cod, cu teste)

1. `status='accepted'` este singura valoare care intră în graf și în agregare. `proposed` se
   afișează doar în panoul de analiză.
2. `evidence_quote` trebuie să se potrivească, după normalizare (spații + lowercase), cu
   `page_texts.text[start:end]` pentru `(entry_id, evidence_page)`. Altfel 422. Fără excepții —
   regula anti-halucinație din brief.
3. `direction='neutral'` **nu** cere citat: „nu am găsit dovezi" este un rezultat legitim. Toate
   celelalte direcții îl cer.
4. O singură muchie `accepted` per `(claim_id, entry_id, aspect)` — un studiu nu poate susține și
   contrazice același aspect. Conflictele se rezolvă explicit (utilizatorul alege), nu silent.
5. `proposed_by='user'` la acceptare: dacă utilizatorul schimbă direcția, direcția originală a
   modelului se păstrează în `reasoning`-ul deciziei (audit), nu se pierde.

## 3. Agregarea la nivel de studiu — derivată, nestocată

Nu se salvează un verdict agregat. Se calculează la citire, prin reguli pure:

```
accepted = muchii cu status='accepted' pentru (entry_id, claim_id)
dacă accepted lipsește            -> 'insufficient'
dacă toate 'supports'             -> 'supports'
dacă toate 'contradicts'          -> 'contradicts'
dacă mix                          -> 'mixed'
dacă doar 'neutral'               -> 'neutral'
```

La nivel de claim (pentru culoarea din Theory Cloud) se agregă studiile, cu prag de încredere:
un claim e colorat doar dacă are minim 2 studii acceptate; sub asta, gri, cu contor vizibil
(„1 studiu"). Altfel un singur studiu ar colora tot norul.

## 4. Endpoint-uri (forma minimă)

| Metodă | Rută | Rol |
|---|---|---|
| GET | `/api/theories/tree?node_type=` | arborele existent, filtrat pe tip |
| POST | `/api/claims` | creează claim (manual sau propus de AI, `proposed_by`) |
| GET | `/api/claims/{id}/stances` | muchiile, grupate pe status |
| POST | `/api/claims/{id}/stances` | propune o muchie (AI sau om) |
| PATCH | `/api/claims/stances/{id}` | accept / reject / editare direcție |
| GET | `/api/claims/{id}/aggregate` | agregatul pe studii + pe claim, calculat |
| GET | `/api/entries/{id}/analysis` | panoul studiului: claim-uri atinse, propuneri, bias |

## 5. Unde trăiesc cele trei suprafețe (decis după audit, cu dovezi)

Auditul țintit a arătat că două din cele trei „vederi noi" din brief sunt deja construite în proporție
mare. Construirea lor ca vederi noi ar produce exact redundanța pe care brief-ul o interzice.

| Suprafață | Ce există deja | Decizie |
|---|---|---|
| Theory Cloud | `TheoriesView` randează arborele theory→child cu culori pe status și contoare pro/contra, plus panou de dovezi cu citate. Îi lipsesc: nodurile de tip claim, stance pe 4 valori, muchiile colorate. | **Extindere în `TheoriesView`**, nu vedere nouă. |
| Thesis Outline | `ThesisEvidenceMap` leagă deja secțiuni → claim-uri → dovezi, cu badge `supports`/`contradicts`/`contextualizes` și fragmente PDF cu pagină. Îi lipsește doar saltul către nodul din graf. | **Extindere în `ThesisEvidenceMap`**, nu vedere nouă. |
| Study Analysis Panel | Nu există. `EntryDetail` e pagină întreagă cu tab-uri, `StudyPdfModal` e centrat pe PDF, `StudyQA` e întrebare-răspuns. Niciuna nu arată claim-urile atinse de un studiu + stance propus + citat. | **Singura suprafață nouă** — un panou lateral contextual reutilizabil. |

Pattern pentru panoul nou: `GraphView` are deja un panou lateral (`aside lg:w-80`) cu vecinii nodului,
iar `ChatPanel` are comportamentul mobil corect (overlay full-screen sub `sm`, coloană statică peste).
Panoul se construiește o dată, ca componentă partajată, și se deschide din bibliotecă, graf, teorii și
`EntryDetail` — nu de patru ori separat.

Regulă de proprietate, ca să nu se dubleze informația: `TheoriesView` deține ierarhia de teorii și
stance-ul pe claim; `GraphView` deține muchiile cauzale/empirice între concepte; `ThesisEvidenceMap`
deține legătura capitole→claim-uri; panoul lateral deține analiza unui studiu.

## 6. Ce NU facem în această iterație

- fără RAG peste literatură metodologică (arhitectura o permite, corpusul nu există);
- fără creare automată de claim-uri fără confirmare;
- fără tabelă de verdict agregat (ar deveni a doua sursă de adevăr);
- `theory_evidence` rămâne cum e, pentru TheoriesView, până decidem migrarea la `claim_stance`.

## 6. Riscul principal, spus o dată

Un `claim_stance` acceptat dintr-un click grăbit otrăvește agregarea pe termen lung, iar agregarea
e vizibilă în graf. De aceea ordinea în UI este: omul scrie verdictul → AI-ul își arată propunerea
și citatul → omul compară. Nu invers.
