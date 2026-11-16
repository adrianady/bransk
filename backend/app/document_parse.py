"""Per-page document parsing: native text first, OCR then cloud verification.

Slice 3 of the stance/evidence work (``docs/research/phase0-stance-evidence-map.md``
§4, §5.1). A study PDF is one of three things per page:

* ``native``  — the page has a usable text layer (``get_text()`` longer than
  :data:`NATIVE_MIN_CHARS`); the existing :mod:`app.page_text` pipeline stores
  it as canonical text. Native pages are never sent through OCR or a model.
* ``scanned`` — no/thin text but the page carries an image. The page is rendered
  to PNG, read locally with the CPU Tesseract binary, and *always* checked
  against the image by the explicitly selected cloud vision model. Only a
  cloud verdict that confirms the OCR reading marks the page ``vision-ok``;
  anything else stays ``vision-uncertain``. Both readings are preserved.
* ``empty``   — no text and no image (a blank or broken page).

``empty`` is a *rejection*, not a prompt: asking a vision model to transcribe a
blank page produces invented content. One real entry in the library (278) has a
valid-but-empty one-page PDF, so a whole document can be ``empty`` too — in that
case :func:`parse_document` never calls a model and never writes ``page_texts``.

Design constraints honoured here:

* Local OCR is CPU-only (D2) and uses the ``tesseract`` executable through a
  subprocess. No Python OCR package is imported, and the executable plus the
  requested language packs are detected before a page is read; a missing binary
  or pack raises :class:`OcrUnavailableError` with an actionable message.
* Cloud verification is mandatory for every scanned page, even when the local
  reading looks clean. There is deliberately no heuristic that skips it.
* The cloud model/provider is exactly the one the caller selected. No implicit
  provider chain is used here: without an explicit provider and model the page
  stays uncertain instead of leaking the image to a default endpoint.
* Vision is agnostic of provider — capability is checked per *model* through
  ``llm.model_supports_images`` (D0). Only an explicit ``False`` blocks the run;
  ``None`` (provider does not publish the field) proceeds and lets the provider
  answer, because many compatible endpoints omit the catalogue field.
* A missing or failed cloud call makes the page ``vision-uncertain``. It never
  becomes verified, and the local OCR text is never relabelled as checked.
* A verbatim text match between OCR and cloud output is treated as agreement
  evidence only. It does not by itself prove visual correctness: the cloud
  verdict must also confirm the reading.
* Existing ``manual`` rows and rows referenced by an anchored highlight are
  immutable: neither a normal run nor ``force`` may replace them.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from app.llm import (
    VisionUnsupportedError,
    complete_with_images,
    model_supports_images,
)
from app.page_text import ensure_page_texts, get_page_text

logger = logging.getLogger("chronokg.document_parse")


class OcrUnavailableError(RuntimeError):
    """The local CPU OCR path cannot run (missing binary, language pack).

    This is a configuration problem, not a page problem: the message is shown
    to the user as-is and must name what is missing and how to fix it.
    """


#: A page needs more than this many stripped characters to count as native.
#: Below the threshold the text layer is usually a running header, a page
#: number, or OCR debris — not something a highlight can anchor to.
NATIVE_MIN_CHARS = 50

#: Minimum confident transcription length. Below this a "successful" model
#: answer is almost always a caption, a page number, or a refusal in disguise.
MIN_VISION_CHARS = 120

#: Local OCR language selection. Comma-free Tesseract notation ("eng", "ron",
#: or "eng+ron"). Read at call time so a deployment can change it without a
#: code edit; the default is the language most academic PDFs are printed in.
DEFAULT_OCR_LANG = "eng"

#: Hard ceiling for a single Tesseract invocation (CPU OCR can be slow on very
#: large pages, but a hung process must not freeze an import forever).
OCR_TIMEOUT_SECONDS = 180

#: Cloud verification prompt. The model is given the page image *and* the local
#: OCR reading and must judge the reading, not silently replace it. `verdict`
#: is the only field that can confirm a page; `text` is the model's own reading
#: and is preserved alongside the OCR text when the two disagree.
VERIFICATION_PROMPT = """You are a strict OCR verification engine. You receive one page image and the CPU OCR transcription of that image.

Compare the supplied OCR text against the image, character by character, and answer with a single JSON object:

{"verdict": "match" | "mismatch" | "uncertain", "text": "<your own transcription of the visible text>", "notes": "<short reason>"}

Rules:
1. "match" only when the OCR text is a faithful transcription of the image (whitespace and line breaks may differ; wording, numbers, punctuation and order may not).
2. "mismatch" when the OCR text omits, invents, or misreads visible content. In that case put the correct reading in "text".
3. "uncertain" when the image is unreadable, ambiguous, or you cannot judge it confidently.
4. Never invent text that is not visible. If the image has no text, use an empty "text" and verdict "uncertain".
5. Never summarise, paraphrase, translate, or explain outside the "notes" field.
6. Output only the JSON object: no markdown fences, no preamble, no commentary.

OCR text starts after the marker line.
--- OCR TEXT START ---
{ocr_text}
--- OCR TEXT END ---
"""

_UNCERTAIN_MARKER = "[UNCERTAIN]"
_NO_TEXT_MARKER = "[NO_TEXT]"

# Meta/refusal openers. A model that answers these is describing the task
# instead of doing it, and its output must never be stored as page text.
_REFUSAL_PREFIXES = (
    "i cannot",
    "i can't",
    "i can not",
    "i'm sorry",
    "i am sorry",
    "sorry,",
    "as an ai",
    "as a language model",
    "i'm unable",
    "i am unable",
    "i'm not able",
    "i am not able",
    "unfortunately,",
)

# A response that contains a sizeable slice of the instructions has echoed the
# prompt instead of transcribing the page.
_PROMPT_ECHO_MARKERS = (
    "transcribe every printed character",
    "natural reading order",
    "preserve line breaks exactly",
    "output only the transcribed text",
)

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*$")


# ── Classification ───────────────────────────────────────────────────────────


def _page_kind(page) -> str:
    """Classify one open PyMuPDF page as native / scanned / empty."""
    try:
        text = page.get_text() or ""
    except Exception:  # noqa: BLE001 - a broken page is 'empty', not fatal
        text = ""
    if len(text.strip()) > NATIVE_MIN_CHARS:
        return "native"
    try:
        has_image = bool(page.get_images())
    except Exception:  # noqa: BLE001
        has_image = False
    return "scanned" if has_image else "empty"


def detect_kind(path) -> dict:
    """Classify a PDF page by page.

    Returns ``{pages, pages_native, pages_scanned, pages_empty, kind,
    page_kinds, reason?}`` where ``kind`` is one of ``native``, ``scanned``,
    ``mixed``, ``empty``. ``page_kinds`` is the 1-based ordered list of page
    classifications, so callers can act per page without reopening the file.

    An unreadable/broken PDF is reported as ``kind='empty'`` with ``pages=0``
    and a ``reason`` — it must never be sent to a vision model.
    """
    counts = {"native": 0, "scanned": 0, "empty": 0}
    page_kinds: list[str] = []
    reason = ""
    try:
        import pymupdf

        doc = pymupdf.open(str(path))
    except Exception as exc:  # noqa: BLE001 - broken file is a first-class case
        logger.info("Cannot open %s for classification: %s", path, exc)
        return {
            "pages": 0,
            "pages_native": 0,
            "pages_scanned": 0,
            "pages_empty": 0,
            "kind": "empty",
            "page_kinds": [],
            "reason": "unreadable pdf",
        }
    try:
        for page in doc:
            kind = _page_kind(page)
            page_kinds.append(kind)
            counts[kind] += 1
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass

    pages = len(page_kinds)
    if pages == 0:
        kind = "empty"
        reason = "no pages"
    elif counts["native"] == pages:
        kind = "native"
    elif counts["scanned"] == pages:
        kind = "scanned"
    elif counts["empty"] == pages:
        kind = "empty"
        reason = "no text and no images on any page"
    else:
        kind = "mixed"
    return {
        "pages": pages,
        "pages_native": counts["native"],
        "pages_scanned": counts["scanned"],
        "pages_empty": counts["empty"],
        "kind": kind,
        "page_kinds": page_kinds,
        **({"reason": reason} if reason else {}),
    }


# ── Rendering and page images ────────────────────────────────────────────────


def render_page_png(path, page_number: int, dpi: int = 150) -> bytes:
    """Render one 1-based page to PNG bytes (CPU only, no side effects)."""
    import pymupdf

    doc = pymupdf.open(str(path))
    try:
        if page_number < 1 or page_number > len(doc):
            raise ValueError(f"Page {page_number} is outside 1..{len(doc)}")
        page = doc[page_number - 1]
        return page.get_pixmap(dpi=dpi).tobytes("png")
    finally:
        doc.close()


def _studies_root() -> Path:
    """Resolve the studies root the same way :mod:`app.studies_store` does.

    Read at call time so tests that set ``CHRONOKG_STUDIES_DIR`` after import
    still land in their own folder.
    """
    configured = os.getenv("CHRONOKG_STUDIES_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent / "data" / "studies"


def page_image_path(entry_id: int, page_number: int) -> Path:
    """Destination of a rendered page: ``studies/pages/<entry>/<page>.png``."""
    return _studies_root() / "pages" / str(int(entry_id)) / f"{int(page_number)}.png"


def _write_png(dest: Path, png: bytes) -> None:
    """Atomic PNG write; creates the parent directory only on a real write."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        tmp.write_bytes(png)
        os.replace(tmp, dest)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def save_page_image(path, entry_id: int, page_number: int, dpi: int = 150,
                    force: bool = False) -> Path:
    """Render *page_number* of *path* and store it, once.

    Returns the destination path. The directory is created only when a write
    actually happens, so a dry classification leaves no trace on disk.
    """
    dest = page_image_path(entry_id, page_number)
    if dest.exists() and not force:
        return dest
    _write_png(dest, render_page_png(path, page_number, dpi=dpi))
    return dest


# ── Local CPU OCR (Tesseract executable) ─────────────────────────────────────


def _ocr_lang() -> str:
    return (os.getenv("CHRONOKG_OCR_LANG") or DEFAULT_OCR_LANG).strip() or DEFAULT_OCR_LANG


def tesseract_info() -> dict:
    """Detect the Tesseract executable and its installed language packs.

    Returns ``{available, path, languages, requested, error}``. Detection never
    raises: callers decide whether an unavailable OCR path is fatal.
    """
    requested = _ocr_lang()
    info = {
        "available": False,
        "path": "",
        "languages": [],
        "requested": requested,
        "error": "",
    }
    executable = shutil.which("tesseract")
    if not executable:
        info["error"] = (
            "OCR local indisponibil: executabilul 'tesseract' nu a fost găsit în PATH. "
            "Instalează Tesseract OCR (pachetul de sistem) sau configurează PATH-ul; "
            "paginile scanate nu pot fi citite fără el."
        )
        return info
    info["path"] = executable
    try:
        proc = subprocess.run(
            [executable, "--list-langs"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        info["error"] = (
            f"OCR local indisponibil: 'tesseract' există la {executable}, dar "
            f"lista de limbi nu a putut fi citită ({type(exc).__name__}: {exc})."
        )
        return info
    output = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    languages: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or "list of available languages" in line.lower():
            continue
        if re.fullmatch(r"[A-Za-z0-9_+-]+", line):
            languages.append(line)
    info["languages"] = languages
    if proc.returncode != 0 or not languages:
        info["error"] = (
            "OCR local indisponibil: 'tesseract' rulează, dar nu a raportat nicio "
            "limbă instalată. Instalează cel puțin pachetul de limbă engleză (eng)."
        )
        return info
    missing = [
        part for part in requested.split("+")
        if part.strip() and part.strip() not in languages
    ]
    info["available"] = True
    if missing:
        info["error"] = (
            f"OCR local indisponibil: pachetul de limbă '{', '.join(missing)}' nu este "
            f"instalat pentru Tesseract. Limbi disponibile: {', '.join(languages)}. "
            "Instalează pachetul lipsă sau setează CHRONOKG_OCR_LANG."
        )
    return info


def run_tesseract_ocr(png: bytes, lang: str | None = None) -> dict:
    """Read one rendered page with the local Tesseract executable (CPU only).

    Returns ``{text, lang, elapsed_ms}``. Raises :class:`OcrUnavailableError`
    when the executable, the language pack, or the invocation itself fails —
    never a silent empty result, because "no text" must mean the page has no
    text, not that OCR was misconfigured.
    """
    requested = (lang or _ocr_lang()).strip() or DEFAULT_OCR_LANG
    info = tesseract_info()
    if not info["available"]:
        raise OcrUnavailableError(info["error"])
    missing = [
        part for part in requested.split("+")
        if part.strip() and part.strip() not in info["languages"]
    ]
    if missing:
        raise OcrUnavailableError(
            f"OCR local indisponibil: pachetul de limbă '{', '.join(missing)}' nu "
            f"este instalat pentru Tesseract. Limbi disponibile: "
            f"{', '.join(info['languages'])}."
        )

    started = _now_ms()
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            prefix="chronokg_ocr_", suffix=".png", delete=False
        ) as handle:
            handle.write(png)
            tmp_name = handle.name
        proc = subprocess.run(
            [info["path"], tmp_name, "stdout", "-l", requested, "--psm", "3"],
            capture_output=True, text=True, timeout=OCR_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrUnavailableError(
            f"OCR local indisponibil: Tesseract a depășit limita de "
            f"{OCR_TIMEOUT_SECONDS}s pentru această pagină."
        ) from exc
    except OSError as exc:
        raise OcrUnavailableError(
            f"OCR local indisponibil: Tesseract nu a putut fi pornit "
            f"({type(exc).__name__}: {exc})."
        ) from exc
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        detail_text = detail[-1] if detail else f"cod {proc.returncode}"
        raise OcrUnavailableError(
            f"OCR local indisponibil: Tesseract a eșuat pentru această pagină "
            f"({detail_text})."
        )
    # Tesseract terminates the page with a form feed.
    text = (proc.stdout or "").replace("\x0c", "").strip()
    return {
        "text": text,
        "lang": requested,
        "elapsed_ms": _now_ms() - started,
    }


def page_verification_path(entry_id: int, page_number: int) -> Path:
    """JSON sidecar that preserves both readings for one scanned page."""
    return _studies_root() / "pages" / str(int(entry_id)) / (
        f"{int(page_number)}.verification.json"
    )


def _write_verification(dest: Path, payload: dict) -> None:
    """Atomic write of the OCR/cloud verification artifact (best effort)."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, dest)
    except OSError as exc:  # noqa: BLE001 - artifact is evidence, not the anchor
        logger.info("Cannot write verification artifact %s: %s", dest, exc)
        try:
            dest.with_name(dest.name + ".part").unlink(missing_ok=True)
        except OSError:
            pass


# ── Vision transcription and deterministic gates ─────────────────────────────


def _vision_text(result) -> str:
    """Accept ``(provider, model, text)``, a bare string, or an exception."""
    if isinstance(result, str):
        return result
    if isinstance(result, (tuple, list)) and len(result) >= 3:
        return str(result[2] or "")
    return str(result or "")


def _maybe_await(value):
    """Run an awaitable to completion when a mock/caller returned one.

    Production ``llm.complete_with_images`` is async; tests monkeypatch it with
    a plain function. Both work, and calling from inside an event loop does not
    explode.
    """
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    # A loop is already running: run the coroutine on a private loop in a
    # worker thread so the caller's loop is never blocked by nested asyncio.
    box: dict = {}

    def _runner():
        try:
            box["value"] = asyncio.run(value)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _strip_fences(text: str) -> str:
    """Drop markdown code fences a model may wrap the transcription in."""
    lines = (text or "").splitlines()
    while lines and _FENCE_RE.match(lines[0]):
        lines.pop(0)
    while lines and _FENCE_RE.match(lines[-1]):
        lines.pop()
    return "\n".join(lines)


def _is_refusal(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    for prefix in _REFUSAL_PREFIXES:
        if lowered.startswith(prefix):
            return True
    return False


def _echoes_prompt(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _PROMPT_ECHO_MARKERS)


def _minimal_period(words: list[str], limit: int = 48) -> int:
    """Smallest block length that repeats over the whole list, else len(words)."""
    n = len(words)
    for period in range(1, min(n, limit) + 1):
        if n % period:
            continue
        if all(words[i] == words[i % period] for i in range(n)):
            return period
    return n


def _has_repetition_loop(text: str) -> bool:
    """True when a *long* block repeats consecutively three or more times.

    The repeated unit must be at least 12 words. Short refrains (a chorus line,
    a repeated three-word phrase) are legitimate page content and must not be
    flagged; a real model loop repeats a whole sentence back-to-back, and the
    minimal-period check distinguishes the two.
    """
    words = (text or "").split()
    if len(words) < 36:
        return False
    window = 12
    for start in range(len(words) - 3 * window + 1):
        block = words[start:start + 3 * window]
        if block[:window] != block[window:2 * window]:
            continue
        if block[:window] != block[2 * window:3 * window]:
            continue
        if _minimal_period(block) >= window:
            return True
    return False


def _non_printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    allowed = {"\n", "\t", "\r"}
    bad = sum(1 for ch in text if ch not in allowed and not ch.isprintable())
    return bad / len(text)


def _gate(text: str) -> tuple[str, list[str]]:
    """Deterministic quality verdict: ``(status, reasons)``.

    Order matters — the first matching rule wins, so a refusal is never
    re-classified as "just a short page":

    1. empty / ``[NO_TEXT]``      -> uncertain, ``no_text`` (text stored empty)
    2. refusal / prompt echo      -> uncertain, ``model_refusal`` / ``prompt_echoed``
    3. ``[UNCERTAIN]`` marker     -> uncertain, ``uncertain_marker``
    4. short but well-formed text -> ok,``sparse_page`` (informational only)
    5. short and malformed        -> uncertain, ``too_short``
    6. otherwise                  -> loop / non-printable checks as before

    Rule 4 exists because cover pages, publisher pages and front matter are
    genuinely sparse: "Taylor & Francis / http://taylorandfrancis.com" is a
    correct transcription, not a fragment. Flagging it as uncertain was the
    false alarm this rule removes. ``sparse_page`` stays in ``reasons`` so the
    UI can invite a visual check without blocking the text.
    """
    stripped = (text or "").strip()
    if not stripped or _NO_TEXT_MARKER in stripped:
        return ("vision-uncertain", ["no_text"])
    if _is_refusal(stripped):
        return ("vision-uncertain", ["model_refusal"])
    if _echoes_prompt(stripped):
        return ("vision-uncertain", ["prompt_echoed"])
    if _UNCERTAIN_MARKER in stripped:
        return ("vision-uncertain", ["uncertain_marker"])

    if len(stripped) < MIN_VISION_CHARS:
        if _is_well_formed(stripped):
            return ("vision-ok", ["sparse_page"])
        return ("vision-uncertain", ["too_short"])

    reasons: list[str] = []
    if _has_repetition_loop(stripped):
        reasons.append("repetition_loop")
    ratio = _non_printable_ratio(stripped)
    if ratio > 0.05:
        reasons.append(f"non_printable_ratio:{ratio:.2f}")
    return ("vision-ok" if not reasons else "vision-uncertain", reasons)


def _is_well_formed(text: str) -> bool:
    """A sparse page is plausible text, not a fragment of a dense page.

    Requires at least three tokens, at least half the non-space characters
    alphabetic, no token longer than 60 chars (garbage/IDs/base64), and no
    repetition loop. This is deliberately permissive: the point is to separate
    "the page really has three words" from "the model returned noise".
    """
    tokens = (text or "").split()
    if len(tokens) < 3:
        return False
    non_space = [ch for ch in text if not ch.isspace()]
    if not non_space:
        return False
    alphabetic = sum(1 for ch in non_space if ch.isalpha())
    if alphabetic / len(non_space) < 0.5:
        return False
    if any(len(tok) > 60 for tok in tokens):
        return False
    return not _has_repetition_loop(text)


_VERDICTS = ("match", "mismatch", "uncertain")


def _normalize_for_compare(text: str) -> str:
    """Whitespace-insensitive, case-insensitive comparison form.

    Only used to describe agreement between two readings. It never upgrades a
    page to verified by itself: visual correctness requires the cloud verdict.
    """
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


def _parse_verification_reply(raw: str) -> dict:
    """Extract ``{verdict, text, notes}`` from the cloud reply.

    Accepts a bare JSON object with or without fences. Returns an empty dict
    when the reply cannot be interpreted — the caller then marks the page
    uncertain instead of guessing.
    """
    cleaned = _strip_fences((raw or "").strip())
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        verdict = str(data.get("verdict") or "").strip().lower()
        if verdict not in _VERDICTS:
            continue
        return {
            "verdict": verdict,
            "text": str(data.get("text") or ""),
            "notes": str(data.get("notes") or ""),
        }
    return {}


def _cloud_verify(png: bytes, ocr_text: str, *, provider: str | None,
                  model: str | None) -> dict:
    """Ask the explicitly chosen cloud model to judge the OCR reading.

    Returns ``{verdict, text, provider, model, error}``. ``error`` is non-empty
    when no verification happened (no model chosen, provider failed, or the
    reply was unusable); in every such case the page must stay uncertain.
    """
    if not (provider or "").strip() or not (model or "").strip():
        return {
            "verdict": "unavailable",
            "text": "",
            "provider": "",
            "model": "",
            "error": (
                "cloud_not_selected: alege explicit un provider și un model cu "
                "suport pentru imagini; verificarea nu folosește un provider implicit."
            ),
        }
    prompt = VERIFICATION_PROMPT.replace("{ocr_text}", ocr_text or "")
    try:
        raw = _maybe_await(complete_with_images(
            [{"role": "user", "content": prompt}],
            [{"data": png, "mime": "image/png"}],
            provider=provider,
            model=model,
            json_mode=True,
        ))
    except VisionUnsupportedError:
        raise
    except Exception as exc:  # noqa: BLE001 - cloud failure means uncertain
        return {
            "verdict": "unavailable",
            "text": "",
            "provider": provider or "",
            "model": model or "",
            "error": f"cloud_unavailable: {type(exc).__name__}: {exc}"[:300],
        }
    parsed = _parse_verification_reply(_vision_text(raw))
    if not parsed:
        return {
            "verdict": "unavailable",
            "text": "",
            "provider": provider or "",
            "model": model or "",
            "error": "cloud_invalid_reply: răspunsul nu conține un verdict utilizabil.",
        }
    parsed["provider"] = provider or ""
    parsed["model"] = model or ""
    parsed["error"] = ""
    return parsed


def parse_scanned_page(png: bytes, *, provider: str | None = None,
                       model: str | None = None, lang: str | None = None) -> dict:
    """OCR one rendered page locally, then require cloud verification.

    Flow: CPU Tesseract reads the page, then the explicitly selected cloud
    model compares that reading against the image. The cloud step is never
    skipped — not for a long, clean, or plausible reading — and no implicit
    provider is used.

    Returns ``{text, status, reasons, ocr_text, vision_text, verdict, lang,
    provider, model, ocr_elapsed_ms}``. ``text`` is the canonical reading
    (the local OCR text, or the cloud text when OCR found nothing). ``status``
    is ``vision-ok`` only when the cloud verdict is ``match`` *and* the two
    readings agree *and* the deterministic gates pass. Every other outcome is
    ``vision-uncertain`` with machine-readable reasons. Both readings are
    always returned so a disagreement is never flattened into one string.
    """
    ocr = run_tesseract_ocr(png, lang=lang)
    ocr_text = ocr["text"]
    gate_status, gate_reasons = _gate(ocr_text)

    cloud = _cloud_verify(
        png, ocr_text, provider=provider, model=model,
    )
    vision_text = cloud["text"]
    verdict = cloud["verdict"]
    reasons: list[str] = list(gate_reasons)

    if cloud["error"]:
        reasons.extend(cloud["error"].split("; "))
        status = "vision-uncertain"
    elif verdict == "match":
        if _normalize_for_compare(ocr_text) != _normalize_for_compare(vision_text):
            status = "vision-uncertain"
            reasons.append("ocr_cloud_disagreement")
        elif gate_status != "vision-ok":
            # The two readings agree, but a deterministic gate still found the
            # OCR text implausible (too short / repeated / non-printable).
            status = "vision-uncertain"
        else:
            status = "vision-ok"
            reasons.append("ocr_cloud_agreement")
    elif verdict == "mismatch":
        status = "vision-uncertain"
        reasons.append("ocr_cloud_disagreement")
    else:  # uncertain
        status = "vision-uncertain"
        reasons.append("cloud_uncertain")

    # The canonical text is the local reading whenever it exists: it is the
    # first, immutable reading and anchors must not move to a later model's
    # paraphrase. Only when OCR produced nothing may the cloud reading stand in
    # (still uncertain — it was never confirmed against an OCR reading).
    text = ocr_text if ocr_text.strip() else vision_text

    return {
        "text": text,
        "status": status,
        "reasons": reasons,
        "ocr_text": ocr_text,
        "vision_text": vision_text,
        "verdict": verdict,
        "lang": ocr["lang"],
        "provider": cloud["provider"],
        "model": cloud["model"],
        "ocr_elapsed_ms": ocr["elapsed_ms"],
    }


# ── Whole-document orchestration ─────────────────────────────────────────────


def _verify_vision_model(provider: str | None, model: str | None) -> None:
    """Raise when the catalogue says the chosen model cannot take images.

    Only an explicit ``False`` blocks the run. ``None`` means the provider does
    not publish modalities (or is unreachable), so the call is attempted and
    ``llm`` raises a provider-specific error if the model really is text-only.
    """
    if not provider or not model:
        return
    supported = _maybe_await(model_supports_images(provider, model))
    if supported is False:
        raise VisionUnsupportedError(
            f"Modelul {model} al providerului {provider} nu acceptă imagini. "
            "Alege un model cu suport pentru imagini (modalitate de intrare 'image') "
            "sau un alt provider."
        )


def _row_source(conn, entry_id: int, page_number: int) -> tuple[str, str, str]:
    """Return ``(source, status, text)`` for an existing page row, or blanks."""
    try:
        row = conn.execute(
            "SELECT source, status, text FROM page_texts"
            " WHERE entry_id = ? AND page_number = ?",
            [entry_id, page_number],
        ).fetchone()
    except Exception:  # noqa: BLE001 - uninitialised schema behaves as "absent"
        return ("", "", "")
    if row is None:
        return ("", "", "")
    try:
        return (row["source"] or "", row["status"] or "", row["text"] or "")
    except Exception:  # noqa: BLE001 - tuple rows from older drivers
        return (row[0] or "", row[1] or "", row[2] or "")


def _page_is_anchored(conn, entry_id: int, page_number: int) -> bool:
    """True when a highlight already anchors offsets into this page's text.

    Such a row is immutable in practice: replacing it would move or invalidate
    the anchor. A highlight row without offsets (legacy display-only quote) does
    not pin the text.
    """
    try:
        row = conn.execute(
            "SELECT 1 FROM pdf_highlights"
            " WHERE entry_id = ? AND page_number = ?"
            " AND (start_offset IS NOT NULL OR end_offset IS NOT NULL)"
            " LIMIT 1",
            [entry_id, page_number],
        ).fetchone()
    except Exception:  # noqa: BLE001 - uninitialised schema means "no anchors"
        return False
    return row is not None


def _write_vision_row(conn, entry_id: int, page_number: int, text: str,
                      status: str, force: bool = False,
                      anchored: bool = False) -> str:
    """Store a vision row. Returns ``written``, ``rewritten`` or ``skipped``.

    ``force`` may replace a row that already holds vision text — including a
    previously ``vision-uncertain`` row — so a corrected gate can heal stored
    pages. ``source='manual'`` and a page with anchored highlights are never
    overwritten, with or without ``force``: a human edit and an existing anchor
    both outrank any model output.
    """
    source, existing_status, existing_text = _row_source(conn, entry_id, page_number)
    if source == "manual" or anchored:
        return "skipped"
    if (not force and source == "vision" and existing_status == "vision-ok"
            and existing_text.strip()):
        return "skipped"
    conn.execute(
        """INSERT OR REPLACE INTO page_texts
           (entry_id, page_number, text, source, status, char_count)
           VALUES (?, ?, ?, 'vision', ?, ?)""",
        [entry_id, page_number, text, status, len(text)],
    )
    return "rewritten" if source == "vision" else "written"


def _normalize_pages(pages) -> set[int] | None:
    if pages is None:
        return None
    if isinstance(pages, int):
        return {pages}
    try:
        return {int(p) for p in pages}
    except (TypeError, ValueError):
        return None


def parse_document(conn, entry_id: int, path, *, provider: str | None = None,
                   model: str | None = None, pages=None, force: bool = False,
                   dpi: int = 150, on_progress=None) -> dict:
    """Parse a document page by page, mixing native text and vision.

    ``pages`` limits the run to the given 1-based page numbers. ``on_progress``
    receives ``(step, total, page_number, status)`` after every page so a router
    can stream progress. A failing page is recorded as ``status='error'`` and
    the run continues — one bad page never aborts a book.

    A document classified as ``empty`` (no text and no images anywhere) is
    rejected before any model call and no ``page_texts`` rows are written.
    """
    _verify_vision_model(provider, model)

    info = detect_kind(path)
    kind = info["kind"]
    page_kinds = info.get("page_kinds") or []
    if kind == "empty":
        return {
            "status": "empty",
            "kind": "empty",
            "reason": info.get("reason") or "no text and no images",
            "pages": [],
            "parsed": 0,
            "native": 0,
            "vision": 0,
            "uncertain": 0,
            "empty": info.get("pages_empty", 0) or info.get("pages", 0),
        }

    wanted = _normalize_pages(pages)
    total = len(page_kinds)
    selected = [
        n for n in range(1, total + 1) if wanted is None or n in wanted
    ]

    # Native pages: the existing canonical-text pipeline owns them. It writes
    # the whole document idempotently, so call it once when the selection can
    # contain native text.
    if any(page_kinds[n - 1] == "native" for n in selected):
        ensure_page_texts(conn, entry_id, path, force=force)

    results: list[dict] = []
    counters = {"native": 0, "vision": 0, "uncertain": 0, "empty": 0,
                "error": 0, "rewritten": 0}

    for step, page_number in enumerate(selected, start=1):
        page_kind = page_kinds[page_number - 1]
        entry = {"page": page_number, "kind": page_kind, "status": page_kind,
                 "reason": "", "chars": 0, "elapsed_ms": 0,
                 "elapsed_s": 0.0, "png_bytes": 0, "dpi": dpi}
        started = _now_ms()
        try:
            if page_kind == "native":
                row = get_page_text(conn, entry_id, page_number)
                if row is not None:
                    entry["status"] = row.get("status") or "native"
                    entry["chars"] = int(row.get("char_count") or 0)
                else:
                    entry["reason"] = "native text below storage threshold"
                counters["native"] += 1

            elif page_kind == "empty":
                counters["empty"] += 1
                entry["reason"] = "no text and no images"

            else:  # scanned
                source, existing_status, existing_text = _row_source(
                    conn, entry_id, page_number)
                anchored = _page_is_anchored(conn, entry_id, page_number)
                if source == "manual":
                    entry["status"] = "manual"
                    entry["chars"] = len(existing_text)
                elif anchored:
                    entry["status"] = existing_status or "native"
                    entry["chars"] = len(existing_text)
                    entry["reason"] = "anchored highlight preserved"
                elif (not force and source == "vision"
                        and existing_status == "vision-ok"
                        and existing_text.strip()):
                    entry["status"] = "vision-ok"
                    entry["chars"] = len(existing_text)
                    counters["vision"] += 1
                else:
                    # Render once: the same bytes are stored next to the text
                    # (for visual quote verification), read by local OCR, and
                    # sent to the explicitly selected cloud model.
                    png = render_page_png(path, page_number, dpi=dpi)
                    entry["png_bytes"] = len(png)
                    dest = page_image_path(entry_id, page_number)
                    if force or not dest.exists():
                        _write_png(dest, png)
                    parsed = parse_scanned_page(
                        png, provider=provider, model=model,
                    )
                    outcome = _write_vision_row(
                        conn, entry_id, page_number,
                        parsed["text"], parsed["status"], force=force,
                        anchored=anchored,
                    )
                    _write_verification(page_verification_path(entry_id, page_number), {
                        "entry_id": entry_id,
                        "page_number": page_number,
                        "ocr_text": parsed["ocr_text"],
                        "vision_text": parsed["vision_text"],
                        "verdict": parsed["verdict"],
                        "status": parsed["status"],
                        "reasons": parsed["reasons"],
                        "lang": parsed["lang"],
                        "provider": parsed["provider"],
                        "model": parsed["model"],
                        "ocr_elapsed_ms": parsed["ocr_elapsed_ms"],
                    })
                    entry["ocr_elapsed_ms"] = parsed["ocr_elapsed_ms"]
                    entry["verdict"] = parsed["verdict"]
                    entry["status"] = parsed["status"]
                    entry["chars"] = len(parsed["text"])
                    if parsed["reasons"]:
                        entry["reason"] = ", ".join(parsed["reasons"])
                    if outcome == "skipped" and entry["status"] == "vision-ok":
                        entry["reason"] = entry["reason"] or "manual row preserved"
                    if outcome == "rewritten":
                        entry["rewritten"] = True
                        counters["rewritten"] += 1
                    if parsed["status"] == "vision-ok":
                        counters["vision"] += 1
                    else:
                        counters["uncertain"] += 1
        except (VisionUnsupportedError, OcrUnavailableError):
            # Configuration problems, not page problems: surface them instead
            # of burning one message per page.
            raise
        except Exception as exc:  # noqa: BLE001 - one page must not abort the run
            logger.info("Page %s of entry %s failed: %s", page_number, entry_id, exc)
            entry["status"] = "error"
            entry["reason"] = f"{type(exc).__name__}: {exc}"[:300]
            counters["error"] += 1
        finally:
            entry["elapsed_ms"] = _now_ms() - started
            entry["elapsed_s"] = round(entry["elapsed_ms"] / 1000.0, 3)
            results.append(entry)
            if on_progress is not None:
                try:
                    on_progress(step, len(selected), page_number, entry["status"])
                except Exception:  # noqa: BLE001 - progress is best-effort
                    pass

    try:
        conn.commit()
    except Exception:  # noqa: BLE001
        pass

    return {
        "status": "ok",
        "kind": kind,
        "pages": results,
        "parsed": counters["native"] + counters["vision"] + counters["uncertain"],
        "native": counters["native"],
        "vision": counters["vision"],
        "uncertain": counters["uncertain"],
        "empty": counters["empty"],
        "rewritten": counters["rewritten"],
    }


def _now_ms() -> int:
    import time

    return int(time.monotonic() * 1000)
