# Faza 0 — Raport de decizie: Stance & Evidence Map

Data: 2026-11-13 · Stare: **neaprobat** · Nu s-a scris cod.

Scop: validarea independentă a ipotezelor din brief înainte de faza 1. Fiecare afirmație e marcată
`VERIFICAT` (am confirmat din sursă primară sau din cod), `NECONFIRMAT` (plauzibil, dar sursa nu a
putut fi verificată) sau `INFIRMAT`.

---

## 0. Rezumat executiv

| Temă | Verdict | Consecință |
|---|---|---|
| Strat 1 NLI ca filtru ieftin | **Parțial valid.** Ollama nu poate rula cross-encodere; NLI e antrenat pe propoziții, nu pe pasaje de 500 de cuvinte. | Strat 1 rulează **in-process** (transformers), nu prin Ollama. Necesită split pe propoziții + agregare. Altfel: se sare peste stratul 1. |
| Verdict agregat prin reguli hardcodate | **Valid.** | Rămâne cum e în brief. |
| RoB 2 + ROBINS-I + GRADE ca taxonomie de start | **Infirmat pentru științe sociale.** RoB 2 = doar RCT; ROBINS-I v2 = doar studii de cohortă cu intervenție; GRADE = certitudine pe outcome de intervenție. | Start: **MMAT + CASP + QuADS + ROBINS-I v2 (afișare)**. RoB 2 se adaugă doar când există RCT. |
| Cifrele κ=0,51 / sens 53% / spec 99% | **Verificat** (GPT-4o vs. reviewer Cochrane, n=84 RCT). | Se pot folosi ca argument pentru „human-first". Nu se generalizează la ROBINS-I (acord mai slab). |
| Highlight are „pagină + offset" | **Infirmat.** Are doar `page_number` + `quote` (text), re-ancorat prin potrivire fuzzy la randare. | Offset-urile trebuie **construite**, nu presupuse. Blochează trigger-ul din §3. |
| Chunk-urile se mapează pe pagini cu offset | **Infirmat.** `page_number` da, offset nu; iar normalizarea spațiilor distruge poziția caracterelor. | E nevoie de text canonic per pagină + offset-uri, înainte de orice legătură claim↔evidence. |
| Vision prin DeepSeek | **Valid** (dar un raport intermediar a susținut contrariul). | Cloud vision funcționează cu providerul existent. |
| Vision local <30 s/pagină pe CPU | **NECONFIRMAT.** Estimările variază 15–180 s/pagină în funcție de model. | Se măsoară pe hardware-ul tău înainte de a promite în UI. |

---

## 1. Clasificare stance

**Infirmat:** „un model NLI local prin Ollama". Ollama servește modele generative; cross-encoderele
(deberta-v3 / ModernBERT NLI, 150–435M, Apache 2.0) nu rulează acolo. Variante reale:
rulare in-process în procesul FastAPI (transformers, ~300 MB RAM, CPU) sau un server
OpenAI-compatible care expune `/rerank` (LocalAI).

**Infirmat:** „un pasaj de 500 de cuvinte se evaluează într-un singur pass". Modelele NLI sunt
antrenate pe perechi de propoziții (max ~512 tokeni). Pipeline-ul corect: split pe propoziții →
ferestre de 3–5 propoziții cu stride 1–2 → agregare (max-pool sau LogSumExp). Altfel scorurile
sunt zgomot.

**Confirmat:** stratul 1 rămâne semnal soft; verdictul final se calculează prin reguli.

**Risc real la stratul 2 (LLM):** literatura 2024–2026 raportează trei eșecuri sistematice relevante
aici — (a) *context bias*: dacă îi dai LLM-ului text de context suplimentar, se aliniază la
sentimentul contextului, nu la pasaj (regresii de F1 raportate de până la ~28%); (b) *neutral
under-reporting*: modelele RLHF evită eticheta „neutral", deci claim-urile neatinse ies fals
`supports`/`contradicts`; (c) *position bias*. Mitigări care se pot implementa ieftin și care
trebuie incluse de la început: citat verbal **înainte** de etichetă, două apeluri cu ordinea
opțiunilor inversată, extragere de logprobs pe tokenul de etichetă (nu parsare de text).

**Frameworks de cercetare:** ClaimSpect (ACL 2025, descompunere ierarhică pe aspecte) și
SciFact/FEVER par integrabile; FOLAR și familia LLM→FOL→Z3 sunt research-only, nu componente
drop-in. `NECONFIRMAT`: disponibilitatea și licența repo-urilor ClaimSpect nu au fost verificate
direct — de verificat înainte de a le include ca dependență.

**Recomandare:** strat 1 = `cross-encoder/nli-deberta-v3-large` in-process, split + fereastre +
agregare; strat 2 = orice endpoint OpenAI-compatible, cu citat-verbal-first + logprobs + order-swap;
strat 3 = reguli. Fallback fără NLI: pre-filtru determinist (verbe de afirmație empirică + scope de
negație) și mers direct la stratul 2. **Poarta de calitate:** macro-F1 ≥ 0,78 și recall pe „neutral"
≥ 0,70 pe ~200 perechi (claim, pasaj) adnotate manual, înainte de a avea încredere în pipeline.

---

## 2. Taxonomii de bias

**Infirmat ca set de start.** RoB 2 acoperă exclusiv RCT-uri (5 domenii, 22 de întrebări de
semnalizare). ROBINS-I v2 (existent, confirmat) este scopat explicit pe **studii de cohortă
comparând intervenții fără randomizare** — nu acoperă transversal, caz-control sau calitativ.
GRADE notează certitudinea pe outcome de intervenție. Într-o teză de psihologie/educație, RCT-urile
sunt excepția; un checklist care presupune RCT va fi completat greșit.

**Constrângere de licență (blocantă, nu tehnică).** RoB 2, ROBINS-I, ROBINS-E sunt publicate sub
**CC BY-NC-ND 4.0**: atribuire obligatorie, **necomercial**, **fără opere derivate**. Consecințe
concrete pentru acest repo:
- nu se poate livra un JSON „adaptat/tradus" al întrebărilor de semnalizare;
- ND + un repo public licențiat Apache 2.0 este o combinație incompatibilă dacă materialul e
  distribuit în pachet — textul Cochrane trebuie ținut separat, cu atribuire și link, și exclus
  din distribuția Apache;
- pentru utilizare personală/academică e acceptabil să afișezi checklist-ul verbatim cu atribuire,
  dar nu să îl modifici.

**Alternativa recomandată pentru v1** (acoperă designurile reale, sunt machine-checkable, licențe
utilizabile): **MMAT 2018** (router pe design, 2 întrebări de screening + 5×5 criterii),
**CASP Qualitative** (10 întrebări), **QuADS** (13 itemi, **CC BY 4.0** — singurul permissive din
listă, potrivit pentru chestionare/scale, exact cazul tău), **ROBINS-I v2 doar ca afișare** pentru
studiile de cohortă. `NECONFIRMAT`: termenii exacti de încorporare pentru MMAT (© McGill) și JBI —
de verificat înainte de a le include în build.

**Anchoring — decizie de UI, nu de model.** Cifrele confirmate (κ=0,51; sens 53%; spec 99%; cel mai
slab domeniu: „selective reporting", κ=0,20) spun un singur lucru util: AI-ul e bun la a *curăța*
studiile cu risc mic și prost la a *prinde* riscul mare. Deci ordinea în UI trebuie să fie
**uman întâi, AI după**: reviewerul își înregistrează verdictul pe fiecare domeniu, abia apoi se
dezvăluie propunerea AI + citatele. Alternativa (arăți propunerea AI și ceri confirmare) produce
ancorare documentată și transformă „confirmarea umană" într-un click gol.

---

## 3. Stocarea dovezilor (măsurat în cod, nu presupus)

Ce există azi:

- `pdf_highlights`: `entry_id`, `document_id` (opțional), `page_number`, `quote` (≤4000 car.),
  `note`, `created_at`. **Fără offset-uri, fără rect/quad.**
- `study_notes`: `content`, `page_number`, `course_context` + tabela de proveniență
  `study_note_sources` → `documents`. **Fără offset-uri.**
- `documents`: `chunk_index`, `page_number`, `content`, `embedding`. **Fără offset-uri în pagină.**
- Re-ancorarea highlight-ului la randare: concatenarea span-urilor text-layer, normalizare
  (spații + lowercase), `indexOf(quote)` → Range → rect-uri pictate. Adică **potrivire fuzzy pe
  citat**, nu offset.

Trei defecte măsurate care contrazic direct brief-ul:

1. **Offset-urile sunt imposibile pe datele actuale.** `chunk_text()` face `re.sub(r"\s+", " ")`
   înainte de împărțire (`pdf_rag.py:83`), deci poziția caracterelor din chunk nu mai corespunde
   cu textul paginii extrase. Nu se poate deriva un offset prin simplă concatenare.
2. **Documentele lungi sunt indexate parțial.** `MAX_PDF_CHARS = 60000` (`pdf_rag.py:23`) taie
   textul la ~20 de pagini. Pe un articol de 27 de pagini, ultimele ~7 pagini nu există în RAG.
   Un highlight de pe pagina 24 nu are text corespondent — analiza ar raporta „insufficient
   evidence" pe un citat perfect valid.
3. **Ancorarea cross-pipeline se rupe.** Am comparat PyMuPDF vs. pypdf pe 3 PDF-uri reale din
   bibliotecă: similaritate 1,000 / 0,919 / 1,000 pe pagină — dar pe pagina 3 a unui document
   similaritatea scade la **0,757**. Textul venit din vision va diferi și mai mult (whitespace,
   hyphenation, ordine de citire). Un highlight creat pe text nativ nu se va randa pe text vision
   și invers.

**Implicație de arhitectură:** e nevoie de un **text canonic per pagină** (extras o singură dată,
imutabil, cu `page_text` + `status`), iar toate highlight-urile, notele și claim-urile să
referențieze `(entry_id, page, start_offset, end_offset)` **în acel text**. `quote` rămâne doar
pentru afișare și pentru verificarea anti-halucinație, nu ca ancoră. Fără asta, trigger-ul din §3
al brief-ului („highlight legat de un span exact") nu poate fi implementat corect.

---

## 4. Documente scanate (vision)

**Valid:** parsare pe pagină, cu status per pagină (`native` / `vision-ok` / `vision-uncertain` /
`manual`) și păstrarea imaginii paginii alături de text. Asta e singura variantă care permite
verificarea vizuală a citatelor.

**Corectat față de un raport intermediar:** DeepSeek **are** intrare de imagine nativă
(`deepseek-flash`, format OpenAI-compatible, base64/URL/`file_id`, mesaj de tip user). Varianta
cloud din brief e fezabilă cu providerul curent. Modelele text-only (`deepseek-v4-pro`) returnează
400 la imagine — deci selecția modelului trebuie validată, nu presupusă.

**Local:** familia de modele mici specializate pe documente (olmOCR, LightOnOCR, în variante GGUF
prin Ollama/llama.cpp) există și **nu depinde de Tesseract** — deci respectă constrângerea. Modelele
generaliste de 7B+ (Qwen-VL, Llama 4) sunt prea lente pe CPU pentru batch. `NECONFIRMAT`: cifrele
de throughput (15–35 s/pagină) și cele de acuratețe — trebuie măsurate local.

**Bounding boxes:** modelele specializate întorc regiuni + coordonate; unele generaliste întorc doar
text. Fallback-ul real pentru „decupajul din pagină" este: căutare fuzzy a citatului în textul
extras → mapare pe rect prin text-layer; dacă nu se găsește, se afișează pagina întreagă cu mesajul
„nu am putut localiza exact".

**Eșecuri cunoscute de semnalat în UI:** tabele „netezite" (celule inventate), formule transcrise
ca LaTeX inexistent, markere de note de subsol pierdute, hyphenation la cap de rând, ordine de
citire greșită la layout pe două coloane.

---

## 5. Ce se schimbă în ordinea de implementare

Brief-ul are 12 pași care presupun infrastructura de ancorare deja existentă. Realitatea o impune
înainte:

1. ~~Research (acest document)~~ ✅
2. **Text canonic per pagină + offset-uri** (schelet nou, migrare aditivă, backfill pentru
   highlight-urile existente prin potrivire pe citat). **Blocant pentru tot restul.**
3. **Interfață AI comună** (text + vision) — azi `llm.py` e text-only: nu există niciun path care
   să trimită `image_url`.
4. Abia apoi: parser (nativ + vision), scheme claim/stance, cele 3 straturi, UI.

Recomand **feliat**, nu în 12 pași dintr-o bucată. Prima felie care produce valoare singură:
text canonic + offset-uri + migration + reader-ul care randează din offsets (fără AI deloc). A doua:
parser vision pe un singur document scanat, cu status. A treia: claim tree + stance pe un studiu,
cu confirmare umană. Fiecare felie e livrabilă și testabilă separat.

---

## 5.1. Decizii confirmate de utilizator (2026-11-13)

- **D0 — Vision agnostic de provider, obligatoriu.** Nu există „providerul cu vision". Orice provider
  selectabil (Ollama, DeepSeek, OpenCode Console, custom, openai-compatible) trebuie să funcționeze
  pentru parsare și pentru verificarea vizuală, dacă modelul ales expune intrare de imagine.
  Consecință de design: capabilitatea se **detectează per model**, nu per provider. `model_catalogue()`
  citește deja `input_modalities` de la provider (`llm.py`) și nu îl folosește nicăieri — acesta e
  hook-ul: un model fără modalitate imagine nu poate fi selectat pentru parsare, iar UI-ul explică
  de ce, în loc să eșueze la runtime. Text-only trebuie să returneze o eroare clară (DeepSeek
  returnează 400 pentru `deepseek-v4-pro`, deci diferența e reală și per model, nu per provider).
- **D1 — Verificarea vizuală a documentelor scanate se face cu modele cloud.** Decupajul din pagină
  și confirmarea citatului se leagă de un model cloud cu imagine; parsarea locală e opțională.
- **D2 — Orice prelucrare locală de documente rulează pe CPU + RAM, fără GPU.** Deci modelul local
  trebuie să fie mic și cuantizat (familia OCR-VLM 1–4B, GGUF), cu timp per pagină măsurat pe
  hardware-ul real înainte de a fi oferit ca opțiune implicită.
- **D3 — Licențiere: doar QuADS (CC BY 4.0) intră în cod.** RoB 2 / ROBINS-I / MMAT / CASP rămân
  **afișate cu atribuire și link**, fără JSON derivat, fără text modificat, în afara distribuției
  Apache. Consecință: checklist-ul activ livrat e QuADS + un schelet propriu pentru domeniile de
  risc, iar instrumentele Cochrane se deschid ca referință, nu ca formular pre-completat.

## 6. Decizii care necesită aprobarea ta

| # | Decizie | Recomandarea mea |
|---|---|---|
| D1 | Taxonomie de bias de start | MMAT + CASP + QuADS ca active; RoB 2 / ROBINS-I v2 doar ca afișare cu atribuire. RoB 2 devine activ doar pentru RCT. |
| D2 | Licența Cochrane în repo Apache 2.0 | **Rezolvat:** doar QuADS în cod; Cochrane afișat cu atribuire, în afara distribuției. |
| D3 | Ordinea în UI la bias | Uman întâi, AI după (anti-anchoring). Costă un pas în plus în flux, dar e singura variantă care face „confirmarea" reală. |
| D4 | Sursa de adevăr pentru evidence | Text canonic per pagină + offset-uri; `quote` doar pentru afișare/verificare. Accept că asta cere migrare + backfill înainte de feature. |
| D5 | Stratul 1 NLI | In-process transformers, cu split pe propoziții. Dacă refuzi dependența nouă, se sare peste stratul 1 (LLM + pre-filtru determinist). |
| D6 | Plafonul `MAX_PDF_CHARS` | Ridicat (sau eliminat) — altfel cărțile și articolele lungi rămân parțial nevăzute de AI. Are cost: indexare mai lentă și spațiu în DB. |

---

## 7. Riscuri de scop, spuse direct

- Feature-ul atinge simultan: schema DB, pipeline PDF, abstracția AI, graph-ul, plus trei vederi noi.
  Estimarea onestă pe tot ce e în brief: **săptămâni, nu zile**, iar primele două treimi sunt
  infrastructură nevăzută (offset-uri, vision, migrare).
- Auditul UI din §1.5 e o condiție sănătoasă, dar la scară completă e el însuși un proiect. Am
  făcut deja o trecere de simplificare pe bibliotecă/telefon; recomand ca auditul rămas să fie
  **țintit** pe zonele pe care feature-ul le atinge (reader, graf, teorii, claim-uri), nu pe tot UI-ul.
- Riscul cel mai mare nu e tehnic, e de încredere: un verdict de stance greșit, afișat convingător,
  într-o teză, e mai rău decât lipsa feature-ului. De aceea poarta de calitate (macro-F1 ≥ 0,78) și
  citatul verificabil sunt non-negociabile, iar „insufficient evidence" trebuie să fie un rezultat
  acceptat, nu o eroare de evitat.

---

## Anexă — măsurători proprii (reproductibile)

| Măsurătoare | Rezultat |
|---|---|
| `pdf_highlights` are offset-uri | nu — doar `page_number` + `quote` (`db.py:476`) |
| `study_notes` are offset-uri | nu — `page_number` + `content` (`db.py:258`) |
| `documents` are offset în pagină | nu — `chunk_index`, `page_number`, `content` (`db.py:246`) |
| Normalizare înainte de chunking | da — `re.sub(r"\s+", " ", text)` (`pdf_rag.py:83`) |
| Plafon text extras | 60.000 caractere total (`pdf_rag.py:23`) |
| PyMuPDF vs pypdf, similaritate pagină | 1,000 / 0,919 / 1,000 (minim 0,757 pe o pagină) |
| Suport imagine în `llm.py` | nu — niciun `image_url` în tot fișierul |
| DeepSeek vision | da — `deepseek-flash`, format OpenAI-compatible |
| RoB 2 licență | CC BY-NC-ND 4.0 (NC + fără derivate) |
| ROBINS-I v2 | există; scopat pe cohortă cu intervenție |
| κ=0,51 / sens 53% / spec 99% | confirmat, GPT-4o vs Cochrane, n=84 RCT |
| Vision cloud real (glm-5.3-flash), pagină 150 dpi | 2,5–4,2 s/pagină, 35 KB (text) – 931 KB (copertă dark) |
| Transcriere reală corectă | da — copertă și pagina de editură, fără traducere, fără inventat |
| Distribuție lungimi bibliotecă | median 16 pagini; 36% peste 20 pagini (RAG trunchiat); max 332 |
| Highlight-uri legate de offset-uri | 7 din 9 (2 ambigue: citatul apare de două ori pe pagină) |
| NLI ONNX (nli-deberta-v3-base, cuantizat) | 233 MB pe disc, **34–153 ms/pereche** pe CPU, fără torch |
| Acuratețe NLI pe formulări științifice | parafrază cu „primary" adăugat → `neutral 0.96` (ratare corectă de semnalare, nu verdict) |

### Amendament la D5 — stratul 1 rulează pe ONNX, nu pe torch

Măsurat: `torch`, `transformers`, `sentence-transformers` lipsesc; `onnxruntime` 1.31 era deja instalat.
Modelul ales: `Xenova/nli-deberta-v3-base` cuantizat (`model_quantized.onnx`, 233 MB) + `tokenizer.json`,
sub `backend/data/models/` (neversionat). S-a adăugat o singură dependență: `tokenizers` 0.23.3.

Ordinea etichetelor se citește din `config.json` (`0=contradiction, 1=entailment, 2=neutral`), nu se
hardcodează. Viteză: 34–153 ms per pereche la batch 1, deci un studiu cu 10 highlight-uri × ~6 ferestre
înseamnă sub 5 secunde pentru tot stratul 1 — mult sub bugetul de 2 minute din criteriile de acceptanță.

Observație care confirmă arhitectura: pe „Proximal processes are the engines" vs. „the primary engines"
modelul dă `neutral 0.96`, adică ratează o susținere reală. Exact de aceea stratul 1 rămâne semnal, iar
verdictul se calculează prin reguli, cu LLM-ul chemat doar pe ferestrele puternice.
