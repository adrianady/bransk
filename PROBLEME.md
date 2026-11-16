# PROBLEME.md — Jurnal QA, lucrare academică end-to-end (Bransk)

Scop: registrul defectelor și al verificărilor din rularea academică reală, cap-coadă,
prin interfața Bransk. Fiecare intrare: ID · etapă · reproducere · așteptat/obținut ·
severitate · dovadă (UI) · cauză · remediere · retestare.

Reguli: tot ce ține de cercetare se face DOAR în browser. Codul/terminalul se folosesc
doar pentru diagnosticare și reparare, iar fiecare reparare se retestează în browser.
Date reale de cercetare nu se modifică destructiv; pentru teste se folosesc fixture-uri
sintetice marcate și șterse după verificare.

Severitate: blocant | major | minor | cosmetic.

---

## VERIFICĂRI (fixuri confirmate în browser)

### QA-001 — Rutare AI către provideri online/GPU (CPU/RAM only)
- Etapă: configurare AI / resurse.
- Reproducere: deschide „Stare și setări AI” în browser; observă modul și lista de provideri.
- Așteptat: numai endpoint loopback și Mock permise; niciun provider cloud; local failure fără fallback online.
- Obținut: „Mod furnizor: CPU-only”; toți providerii cloud (DeepSeek, OpenAI, OpenRouter, Groq, Mistral, Together, NVIDIA, OpenCode, Ollama) afișează „Blocat” și sunt dezactivați; „Personalizat (local Qwen2.5-Coder 7B)” este Activ; mesaj explicit „Dacă modelul local nu răspunde, cererea se oprește; nu există fallback online”.
- Dovadă UI: snapshot dialog „Furnizori AI și stare” (2026-11-16); buton „Testează conexiunea” → „✓ 1266ms” pe cardul Personalizat.
- Cauză ( din sesiunea anterioară): `.env` cu `LLM_PROVIDER=auto`, fallback `deepseek → custom → nvidia`; o probă `/api/chat` ajunsese la `nvidia`.
- Remediere: `backend/app/cpu_only.py` + bariere în `llm.py`, `embeddings.py`, `document_parse.py`, `routers/ai.py`, `routers/notes.py`; UI marchează/disactivează providerii blocați.
- Retestare: VERIFICAT ÎN BROWSER (2026-11-16) — atât politica afișată, cât și răspunsul efectiv al modelului local pe CPU. Teste tehnice: `pytest tests/test_cpu_only_policy.py` → 7 passed.

### QA-002 — Facet „Citite/Cărți” bloca panoul de notițe
- Etapă: bibliotecă / filtre.
- Reproducere: pilula „Notițe & idei” activă, conținut = studii filtrate; reload păstra starea contradictorie.
- Așteptat/Obținut: așteptat 45 notițe; obținut studii filtr după facet.
- Severitate: major.
- Dovadă UI: sesiunea anterioară, reload cu `quickfacet=read, filter=notes`.
- Cauză: `quickFacet` persistat în sessionStorage; `showingStudies = ... || quickFacet !== ''` făcea panoul inaccesibil.
- Remediere: `LibraryView.tsx` — curățare `quickFacet` la 'all'/'notes' înainte de early-return + normalizare la montare.
- Retestare: VERIFICAT ÎN BROWSER (sesiune anterioară) — 45 notițe după fix; build OK; 96 teste frontend passed.

### QA-003 — Detaliu intrare „Fără conținut” + risc de suprascriere
- Etapă: bibliotecă / detaliu intrare.
- Reproducere: deschide detaliul unei intrări din listă; butonul Editează pornea cu conținut gol.
- Așteptat/Obținut: așteptat textul complet; obținut „Fără conținut” deși `content_preview` exista.
- Severitate: major (risc de pierdere date).
- Dovadă UI: sesiunea anterioară; text și formular după fix (90 chars).
- Cauză: `/api/entries?limit=500` returnează `content: ''` + `content_preview`; `EntryDetail` nu re-fetcha intrarea completă.
- Remediere: `EntryDetail.tsx` — fetch `api.entries.get(id)` când content e gol dar există preview; randare + formular folosesc `entryView`.
- Retestare: VERIFICAT ÎN BROWSER (sesiune anterioară) — detaliul arată textul, editarea păstrează conținutul, delete funcționează (UI + API 404).

---

## CORECȚIE — QA-001 (decizie inversată de utilizator, 2026-11-16)

QA-001 descrie starea dintr-o sesiune anterioară, care **a fost respinsă** de utilizator în sesiunea curentă: regula obligatorie este acum **AI cloud-only**, iar execuția locală e permisă doar pentru embeddings și OCR pe CPU/RAM. Prin urmare, întreaga arhitectură „CPU-only, fără cloud, LLM local Qwen” a fost înlăturată; nu mai constituie stare validă sau dovadă de succes.

### QA-004 — Arhitectură LLM locală / blocarea providerilor cloud (corecția QA-001)
- Etapă: arhitectură AI / configurare (runda 1).
- Reproducere: deschide „Stare și setări AI”; starea era `CPU-only`, provideri cloud cu eticheta „Blocat”, activ = „Personalizat” → `http://127.0.0.1:8198/v1` (Qwen 7B local, ~4,5 GB RAM).
- Așteptat: numai provideri **cloud** pentru sarcinile LLM; niciun model LLM local instalat/încărcat/rulat; fallback automat local imposibil; embeddings/OCR locale rămân pe CPU/RAM.
- Obținut (după remediere): modelul local oprit; `cpu_only.py`, `start-llm-cpu.sh`, `test_cpu_only_policy.py` și toate barierele cloud eliminate; preseturi cloud (DeepSeek, OpenRouter, Groq, OpenCode, etc.) restaurate în UI.
- Dovadă UI: dialog „Furnizori AI și stare” — „Mod furnizor: auto”, DeepSeek/NVIDIA/OpenCode „Configurat”, fără etichete „Blocat”; test NVIDIA → „Conexiune confirmată (2175 ms)”; activare NVIDIA → `/api/ai/status` `active_provider: nvidia`.
- Cauză: sesiune anterioară cu obiectiv opus (CPU-only, fără cloud).
- Remediere: checkout fișiere AI de pe `master` + păstrarea reparațiilor UIUTILIE (LibraryView, EntryDetail, citări, chestionare, export notițe); checkpoint în commitul `e223686` (branch `cloud-only-realign`). Guard nou în `llm.py`: `is_local_endpoint` / `custom_is_local` — endpointurile loopback/private nu pot fi salvate ca „Personalizat”, nu intră în `provider_order()` și nu au URL de chat.
- Retestare tehnică: `pytest tests/test_cloud_only_llm.py` → 11 passed. **Retestare în browser: în curs** (chat NVIDIA + verificare ordin fallback).
- Severitate: blocant (încălca direct regula cloud-only).

## PROBLEME DESCHISE / ÎN LUCRU

_(se completează pe măsură ce rulele end-to-end avansează)_

## REGISTRU RUNDE
| Rundă | Dată | Scop | Rezultat |
|-------|------|------|----------|
| 0 | 2026-11-16 | Baseline & reluare sesiune | App sănătoasă; starea CPU-only din sesiunea anterioară a fost respinsă de utilizator și înlocuită cu cloud-only (QA-004). |
| 1 | 2026-11-16 | Alineare cloud-only + început audit browser | Local LLM oprit, bariere cloud eliminate, NVIDIA cloud activ (test 2175 ms în browser), guard anti-local cu 11 teste verzi. |
