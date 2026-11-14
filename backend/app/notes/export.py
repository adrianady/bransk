"""Offline export of verified note dossiers: Markdown and self-contained HTML."""

import html
import json
import re
import unicodedata

#: Badge class per claim status (mirrors ``app.notes.engine.STATUSES``).
STATUS_CLASSES = {"verificat": "ok", "verificat_cu_nuanta": "nuanta", "incomplet": "atentie", "subestimat": "atentie", "corectat": "corectat", "depasit": "depasit", "neverificat": "neverificat"}

def _str(value) -> str:
    return "" if value is None else str(value)

def _esc(value) -> str:
    """Escape every user/DB value for HTML (quotes, titles, motives)."""
    return html.escape(_str(value), quote=True)

def slugify(title) -> str:
    """Lowercase ASCII slug with ``-`` separators, max 60 chars."""
    text = unicodedata.normalize("NFKD", _str(title))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if len(text) > 60:
        text = text[:60].rstrip("-")
    return text or "dosar"

def _entry_info(payload: dict) -> dict:
    """Title/authors/year tolerating every payload shape the API may give."""
    dossier = payload.get("dossier") or {}
    entry = payload.get("entry") or {}
    if not isinstance(entry, dict):
        entry = {}
    year = (entry.get("year") or entry.get("bib_year") or payload.get("entry_year")
            or dossier.get("year") or dossier.get("bib_year") or "")
    if not _str(year).strip():
        date = entry.get("entry_date") or payload.get("entry_date") or ""
        year = _str(date)[:4] if _str(date).strip() else ""
    title = (dossier.get("title") or payload.get("entry_title")
             or entry.get("title") or payload.get("title") or "")
    authors = (entry.get("authors") or payload.get("entry_authors")
               or dossier.get("authors") or "")
    return {"title": title, "authors": authors, "year": year}

def _source_pairs(payload: dict) -> list:
    """``[(key, value)]`` for Fişa sursei; skips empty values."""
    dossier = payload.get("dossier") or {}
    raw = dossier.get("source_sheet_json")
    data = raw if isinstance(raw, dict) else None
    if data is None and isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            data = parsed if isinstance(parsed, dict) else None
        except Exception:
            data = None
    pairs = [(_str(k), _str(v).strip()) for k, v in (data or {}).items()
             if _str(v).strip()]
    if pairs:
        return pairs
    for section in payload.get("sections") or []:
        if isinstance(section, dict) and section.get("kind") == "fisa_sursei":
            for line in _str(section.get("body_md")).splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    if key.strip() and value.strip():
                        pairs.append((key.strip(), value.strip()))
            break
    return pairs

def _claims(payload: dict) -> list:
    return [c for c in (payload.get("claims") or []) if isinstance(c, dict)]

def _special_sections(payload: dict) -> list:
    return [s for s in (payload.get("sections") or [])
            if isinstance(s, dict) and s.get("kind") != "fisa_sursei"]

def _page_md(claim: dict) -> str:
    """``*p. X (PDF N)*``; the parenthetical drops when ``page_pdf`` is None."""
    printed = _str(claim.get("page_printed")).strip()
    pdf = claim.get("page_pdf")
    has_pdf = pdf is not None and _str(pdf).strip() != ""
    if not printed and not has_pdf:
        return ""
    text = f"p. {printed}" if printed else ""
    if has_pdf:
        text = f"{text} (PDF {pdf})" if text else f"PDF {pdf}"
    return f"*{text}*"

def _cell(value) -> str:
    return _str(value).replace("|", "\\|").replace("\n", " ").strip()

def dossier_markdown(payload: dict) -> str:
    """One dossier as Romanian Markdown (headings in the fixed order)."""
    dossier = payload.get("dossier") or {}
    info = _entry_info(payload)
    claims = _claims(payload)
    summary = payload.get("summary") or {}
    by_status = dict(payload.get("by_status") or {})
    for claim in claims:  # fall back to live counts when summary is missing
        by_status[_str(claim.get("status")) or "neverificat"] = (
            by_status.get(_str(claim.get("status")) or "neverificat", 0))
    total = summary.get("total", len(claims))
    n_ok = summary.get("verificat", by_status.get("verificat", 0))
    n_no = summary.get("neverificat", by_status.get("neverificat", 0))
    lines = [f"# {info['title']}".rstrip()]
    byline = " · ".join(p for p in (info["authors"], _str(info["year"])) if p.strip())
    if byline.strip(" ·"):
        lines.append(f"*{byline}*")
    lines += [f"Dosar: {dossier.get('status') or 'draft'} · Afirmații: {total} · "
              f"Verificate: {n_ok} · Neverificate: {n_no}", "", "## Fișa sursei"]
    lines += [f"{key}: {value}" for key, value in _source_pairs(payload)]
    lines.append("")
    lines.append("## Afirmații")
    for claim in claims:
        code, claim_status = claim.get("code") or "", claim.get("status") or "neverificat"
        lines += ["", f"### {code} — {claim_status}",
                  f"**Ce am notat:** {_str(claim.get('raw_note')).strip() or _str(claim.get('statement'))}",
                  f"**Ce spune sursa:** {_str(claim.get('statement'))}"]
        if _str(claim.get("quote")).strip():
            lines.append(f"> {_str(claim.get('quote')).strip()}")
        if _page_md(claim):
            lines.append(_page_md(claim))
        if _str(claim.get("correction")).strip():
            lines.append(f"**Corecție:** {_str(claim.get('correction')).strip()}")
        engine = _str(claim.get("engine")).strip()
        if engine and engine != "native":
            visual = _str(claim.get("visual_check")).strip() or "—"
            lines.append(f"**Motor:** {engine} · **Verificare vizuală:** {visual}")
    lines += ["", "## Corecții la notițele mele",
              "| Cod | Ce notasem | Status | Cum este în text |",
              "| --- | --- | --- | --- |"]
    for claim in claims:
        noted = _str(claim.get("raw_note")).strip() or _str(claim.get("statement"))
        how = _str(claim.get("quote")).strip() or _str(claim.get("correction")).strip()
        lines.append(f"| {_cell(claim.get('code'))} | {_cell(noted)} | "
                     f"{_cell(claim.get('status'))} | {_cell(how)} |")
    for section in _special_sections(payload):
        lines += ["", f"## {_str(section.get('title')) or _str(section.get('kind'))}"]
        if _str(section.get("body_md")).strip():
            lines.append(_str(section.get("body_md")).strip())
    edges = [e for e in (payload.get("edges") or []) if isinstance(e, dict)]
    if edges:
        lines += ["", "## Hartă"]
        for edge in edges:
            item = (f"- {_str(edge.get('from_code'))} → {_str(edge.get('to_code'))} "
                    f"— {_str(edge.get('motive')).strip()}")
            if _str(edge.get("page")).strip():
                item += f" (p. {_str(edge.get('page')).strip()})"
            lines.append(item)
    lines.append("")
    lines.append("## Jurnal")
    for rnd in payload.get("rounds") or []:
        if isinstance(rnd, dict):
            lines.append(f"- {_str(rnd.get('created_at'))[:10] or '—'} — {_str(rnd.get('summary'))}")
    return "\n".join(lines) + "\n"

def _anchor(prefix: str, *parts: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]+", "", "".join(parts)).lower().strip("_") or "x"
    return f"{prefix}{clean}"

def _dossier_index(payload: dict, prefix: str = "") -> list:
    """``[(anchor, label)]`` in render order; the TOC is built from this."""
    info = _entry_info(payload)
    items = [(_anchor(prefix, "dosar"), _str(info["title"]) or "Dosar")]
    items += [(_anchor(prefix, _str(c.get("code"))),
               f"{_str(c.get('code'))} — {_str(c.get('status')) or 'neverificat'}")
              for c in _claims(payload)]
    items += [(_anchor(prefix, "sec-", _str(s.get("kind"))),
               _str(s.get("title")) or _str(s.get("kind")))
              for s in _special_sections(payload)]
    return items

def _toc_html(items: list) -> str:
    """Same nav twice: sticky column on wide screens, details on narrow."""
    head = "".join(f'<li><a href="#{_esc(a)}">{_esc(label)}</a></li>' for a, label in items[:1])
    if len(items) > 1:
        head += "<li><ul>" + "".join(
            f'<li><a href="#{_esc(a)}">{_esc(label)}</a></li>' for a, label in items[1:]
        ) + "</ul></li>"
    nav = f"<nav><ul>{head}</ul></nav>"
    return (f'<details class="toc-menu"><summary>Cuprins</summary>{nav}</details>'
            f'<div class="layout"><aside class="toc-wrap">'
            f'<p class="toc-title">Cuprins</p>{nav}</aside><main class="content">')

_CSS = (":root{--bg:#faf8f4;--card:#fff;--ink:#1b2130;--muted:#6d7484;--line:#e2ddd2}*{box-sizing:border-box}"
"body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,\"Helvetica Neue\",Arial,sans-serif;font-size:15px;line-height:1.6}"
".wrap{max-width:1100px;margin:0 auto;padding:24px 20px 64px}.masthead{border-bottom:2px solid var(--line);padding-bottom:16px;margin-bottom:20px}"
".kicker{font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--muted)}"
"h1{font-family:Georgia,\"Times New Roman\",serif;font-weight:400;font-size:30px;margin:8px 0;max-width:32em}h2{font-family:Georgia,serif;font-weight:400;font-size:24px}h3{font-family:Georgia,serif;font-weight:400;font-size:19px}.byline,.counts{color:var(--muted);font-size:13px;margin:4px 0}"
".toc-menu{margin:0 0 16px;border:1px solid var(--line);border-radius:10px;background:var(--card);padding:8px 12px}.toc-menu summary{cursor:pointer;font-weight:600}.toc-wrap{display:none}"
".toc-wrap nav ul,.toc-menu nav ul{list-style:none;margin:0;padding:0}.toc-wrap nav li,.toc-menu nav li{margin:4px 0}.toc-wrap nav a,.toc-menu nav a{color:var(--ink);text-decoration:none}.toc-wrap nav a:hover,.toc-menu nav a:hover{text-decoration:underline}.toc-wrap nav ul ul,.toc-menu nav ul ul{margin-left:14px;font-size:13px}.toc-title{font-family:Georgia,serif;font-size:16px;margin:0 0 8px}"
"@media(min-width:1024px){.layout{display:grid;grid-template-columns:250px 1fr;gap:28px;align-items:start}.toc-menu{display:none}.toc-wrap{display:block;position:sticky;top:16px;border:1px solid var(--line);border-radius:12px;background:var(--card);padding:14px 16px;font-size:13px}}"
"section.dossier{border-top:2px solid var(--line);margin-top:28px;padding-top:20px}section.dossier:first-of-type{border-top:0;margin-top:0;padding-top:0}"
".claim{border:1px solid var(--line);border-radius:12px;background:var(--card);padding:14px 16px;margin:14px 0}.claim-head{display:flex;gap:10px;align-items:center;margin-bottom:6px}.code{font-weight:700}"
".badge{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;padding:2px 10px;border-radius:999px;border:1px solid}.badge.ok{color:#2f6b46;border-color:#2f6b46;background:#e8f3ec}.badge.nuanta{color:#17756f;border-color:#17756f;background:#e3f2f0}.badge.atentie{color:#a97a12;border-color:#a97a12;background:#fbf4e3}.badge.corectat{color:#b4472e;border-color:#b4472e;background:#fdf1ec}.badge.depasit{color:#5b7086;border-color:#5b7086;background:#eef1f5}.badge.neverificat{color:#6b7280;border-color:#6b7280;background:#f1f2f4}"
".statement{margin:6px 0}.noted{font-size:13.5px;color:var(--muted)}.page{font-size:13px;color:var(--muted)}"
"blockquote{margin:12px 0;border-left:3px solid var(--line);padding:8px 14px;background:var(--bg);border-radius:0 8px 8px 0;font-style:italic}blockquote footer{font-style:normal;font-size:12px;color:var(--muted);margin-top:4px}"
"p.fix{border-left:3px solid #b4472e;padding:8px 14px;background:#fdf1ec;border-radius:0 8px 8px 0}p.engine{font-size:12.5px;color:var(--muted)}"
"table.sheet{width:100%;border-collapse:collapse;font-size:14px}table.sheet th,table.sheet td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}table.sheet th{width:32%;color:var(--muted);font-weight:600}"
"ul.edges,ul.rounds,ul.concepts{margin:8px 0;padding-left:20px}ul.concepts ul{margin:4px 0;padding-left:20px}footer.colophon{margin-top:24px;padding-top:12px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}.empty{color:var(--muted);font-style:italic}"
"@media (prefers-color-scheme:dark){:root{--bg:#16181d;--card:#1e2129;--ink:#e8e4da;--muted:#a3a8b5;--line:#363b47}p.fix{background:#2b201b}blockquote{background:#16181d}}"
"@media print{@page{margin:18mm}body{background:#fff}.toc-menu{display:none !important}.toc-wrap{display:block !important;position:static;border:0;background:none;padding:0;break-after:page}.layout{display:block}section.dossier{break-before:page}section.dossier:first-of-type{break-before:auto}.claim{break-inside:avoid}blockquote,table{break-inside:avoid}}")

def _claims_html(payload: dict, prefix: str = "") -> str:
    """Claim articles shared by the single-dossier and project documents."""
    parts = []
    for claim in _claims(payload):
        code, status = _str(claim.get("code")), _str(claim.get("status")) or "neverificat"
        badge = STATUS_CLASSES.get(status, "neverificat")
        parts.append(f'<article class="claim" id="{_esc(_anchor(prefix, code))}">')
        parts.append(f'<div class="claim-head"><span class="code">{_esc(code)}</span> '
                     f'<span class="badge {_esc(badge)}">{_esc(status)}</span></div>')
        parts.append(f'<p class="statement">{_esc(claim.get("statement"))}</p>')
        if _str(claim.get("raw_note")).strip():
            parts.append(f'<p class="noted"><strong>Ce am notat:</strong> '
                         f'{_esc(_str(claim.get("raw_note")).strip())}</p>')
        quote, printed = _str(claim.get("quote")).strip(), _str(claim.get("page_printed")).strip()
        pdf, has_pdf = claim.get("page_pdf"), claim.get("page_pdf") is not None and _str(claim.get("page_pdf")).strip() != ""
        bits = ([f"p. {_esc(printed)}"] if printed else []) + ([f"PDF {_esc(pdf)}"] if has_pdf else [])
        if quote:
            parts.append(f"<blockquote>{_esc(quote)}"
                         + (f"<footer>{' · '.join(bits)}</footer>" if bits else "") + "</blockquote>")
        elif bits:
            parts.append(f"<p class=\"page\">{' · '.join(bits)}</p>")
        if _str(claim.get("correction")).strip():
            parts.append(f'<p class="fix"><strong>Corecție:</strong> '
                         f'{_esc(_str(claim.get("correction")).strip())}</p>')
        engine = _str(claim.get("engine")).strip()
        if engine and engine != "native":
            parts.append(f"<p class=\"engine\">Motor: {_esc(engine)} · Verificare "
                         f"vizuală: {_esc(_str(claim.get('visual_check')).strip() or '—')}</p>")
        parts.append("</article>")
    return "\n".join(parts)

def _sections_html(payload: dict, prefix: str = "") -> str:
    """Special (non Fişa sursei) sections shared by both documents."""
    parts = []
    for section in _special_sections(payload):
        title = _str(section.get("title")) or _str(section.get("kind"))
        paras = "".join(f"<p>{'<br>'.join(_esc(l) for l in para.splitlines())}</p>"
                        for para in _str(section.get("body_md")).split("\n\n") if para.strip())
        parts.append(f'<section id="{_esc(_anchor(prefix, "sec-", _str(section.get("kind"))))}">'
                     f"<h3>{_esc(title)}</h3>{paras}</section>")
    return "\n".join(parts)

def _map_html(payload: dict) -> str:
    edges = [e for e in (payload.get("edges") or []) if isinstance(e, dict)]
    if not edges:
        return ""
    items = []
    for edge in edges:
        text = (f"{_esc(edge.get('from_code'))} → {_esc(edge.get('to_code'))} "
                f"— {_esc(_str(edge.get('motive')).strip())}")
        if _str(edge.get("page")).strip():
            text += f" (p. {_esc(_str(edge.get('page')).strip())})"
        items.append(f"<li>{text}</li>")
    return "<h3>Hartă</h3>\n<ul class=\"edges\">\n" + "\n".join(items) + "\n</ul>"

def _rounds_html(payload: dict) -> str:
    rounds = [r for r in (payload.get("rounds") or []) if isinstance(r, dict)]
    if not rounds:
        return "<h3>Jurnal</h3>"
    items = [f"<li>{_esc(_str(r.get('created_at'))[:10] or '—')} — {_esc(r.get('summary'))}</li>"
             for r in rounds]
    return "<h3>Jurnal</h3>\n<ul class=\"rounds\">\n" + "\n".join(items) + "\n</ul>"

def _sheet_html(payload: dict) -> str:
    rows = "".join(f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>" for k, v in _source_pairs(payload))
    return f"<h3>Fișa sursei</h3>\n<table class=\"sheet\"><tbody>\n{rows}\n</tbody></table>"

def _footer_html(payload: dict) -> str:
    dossier = payload.get("dossier") or {}
    by_status = dict(payload.get("by_status") or {})
    claims = _claims(payload)
    if not by_status:
        for claim in claims:
            key = _str(claim.get("status")) or "neverificat"
            by_status[key] = by_status.get(key, 0) + 1
    date = _str(dossier.get("updated_at") or dossier.get("created_at"))[:10] or "—"
    counts = f"Afirmații: {len(claims)}"
    for key in ("verificat", "verificat_cu_nuanta", "neverificat"):
        if by_status.get(key):
            counts += f" · {key}: {by_status[key]}"
    quote_line = ("Toate citatele au fost confruntate cu textul extras al paginii declarate."
                  if by_status.get("verificat", 0) > 0 else "Nicio afirmație nu este încă verificată.")
    return (f"<footer class=\"colophon\">Generat: {_esc(date)} · {_esc(counts)}<br>"
            f"{_esc(quote_line)}</footer>")

def _dossier_section_html(payload: dict, prefix: str = "") -> str:
    """One dossier section; both documents share this block renderer."""
    info = _entry_info(payload)
    parts = [f'<section class="dossier" id="{_esc(_anchor(prefix, "dosar"))}">',
             f"<h2>{_esc(info['title']) or 'Dosar'}</h2>"]
    byline = " · ".join(p for p in (_str(info["authors"]), _str(info["year"])) if p.strip())
    if byline.strip(" ·"):
        parts.append(f'<p class="byline">{_esc(byline)}</p>')
    parts.append(_sheet_html(payload))
    if _claims_html(payload, prefix):
        parts.append("<h3>Afirmații</h3>")
        parts.append(_claims_html(payload, prefix))
    if _sections_html(payload, prefix):
        parts.append(_sections_html(payload, prefix))
    if _map_html(payload):
        parts.append(_map_html(payload))
    parts += [_rounds_html(payload), _footer_html(payload), "</section>"]
    return "\n".join(parts)

def _document(title: str, toc_items: list, body: str) -> str:
    return ("<!DOCTYPE html>\n<html lang=\"ro\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{_esc(title)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n"
            f"<div class=\"wrap\">\n{_toc_html(toc_items)}\n{body}\n</div>\n"
            "</body>\n</html>\n")

def dossier_html(payload: dict) -> str:
    """One dossier as a self-contained offline HTML document."""
    title = _str(_entry_info(payload)["title"]) or "Dosar"
    return _document(title, _dossier_index(payload), _dossier_section_html(payload) + "\n")

def _concepts_of(project: dict, payloads: list) -> list:
    if isinstance(project.get("concepts"), list):
        return [c for c in project["concepts"] if isinstance(c, dict)]
    for payload in payloads:
        if isinstance(payload, dict) and isinstance(payload.get("concepts"), list):
            return [c for c in payload["concepts"] if isinstance(c, dict)]
    return []

def _project_map_html(project: dict, payloads: list) -> str:
    concepts = _concepts_of(project, payloads)
    titles = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        dossier = payload.get("dossier") or {}
        try:
            titles[int(dossier.get("id"))] = _str(_entry_info(payload)["title"]) or f"Dosar {dossier.get('id')}"
        except (TypeError, ValueError):
            continue
    if not concepts:
        return "<h2>Harta generală</h2>\n<p class=\"empty\">Harta nu conține încă concepte verificate.</p>"
    parts = ["<h2>Harta generală</h2>", '<ul class="concepts">']
    for concept in concepts:
        head = f"<strong>{_esc(_str(concept.get('label')) or _str(concept.get('code')))}</strong>"
        if _str(concept.get("description")).strip():
            head += f" — {_esc(_str(concept.get('description')).strip())}"
        parts.append(f"<li>{head}<ul>")
        by_doc: dict = {}
        for link in concept.get("links") or []:
            if not isinstance(link, dict):
                continue
            try:
                by_doc.setdefault(int(link.get("dossier_id")), []).append(link)
            except (TypeError, ValueError):
                continue
        for doc_id in sorted(by_doc):
            parts.append(f"<li>{_esc(titles.get(doc_id, f'Dosar {doc_id}'))}<ul>")
            for link in by_doc[doc_id]:
                line = f"{_esc(link.get('claim_code'))} — {_esc(_str(link.get('note')).strip())}"
                if _str(link.get("page")).strip():
                    line += f" (p. {_esc(_str(link.get('page')).strip())})"
                parts.append(f"<li>{line}</li>")
            parts.append("</ul></li>")
        parts.append("</ul></li>")
    return "\n".join(parts + ["</ul>"])

def project_html(project: dict, payloads: list[dict]) -> str:
    """Whole project: header, concept map as nested lists, dossier sections."""
    project = project if isinstance(project, dict) else {}
    payloads = [p for p in (payloads or []) if isinstance(p, dict)]
    title = _str(project.get("title")) or "Proiect"
    total_ok = sum(1 for p in payloads for c in _claims(p) if _str(c.get("status")) == "verificat")
    byline = " · ".join(p for p in (_str(project.get("author")).strip(),
                                    _str(project.get("work_type")).strip()) if p)
    header = ('<header class="masthead">\n<p class="kicker">Dosare de notițe verificate</p>\n'
              f"<h1>{_esc(title)}</h1>\n")
    if byline:
        header += f'<p class="byline">{_esc(byline)}</p>\n'
    header += (f"<p class=\"counts\">{len(payloads)} dosare · "
               f"{sum(len(_claims(p)) for p in payloads)} afirmații · {total_ok} verificate</p>\n</header>")
    toc_items, sections = [], []
    for payload in payloads:
        try:
            prefix = f"d{int((payload.get('dossier') or {}).get('id'))}-"
        except (TypeError, ValueError):
            prefix = "dx-"
        toc_items.extend(_dossier_index(payload, prefix))
        sections.append(_dossier_section_html(payload, prefix))
    body = (header + "\n" + _project_map_html(project, payloads) + "\n" + "\n".join(sections)
            + '\n<footer class="colophon">Document generat local din dosarele verificate — fără resurse externe.</footer>')
    return _document(title, toc_items, body)
