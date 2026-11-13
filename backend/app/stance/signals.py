"""Layer 1 — stance signals over sentence windows.

**Null means "no NLI available", never 0.0.** A fabricated 0.0 would look like
"neutral" and poison the aggregation, so every probability field is ``None``
until a real scorer exists.

The real scorer is :class:`OnnxNliScorer`: a local DeBERTa-v3 cross-encoder
exported to ONNX (``onnxruntime`` + ``tokenizers``, no ``torch``). Model files
live in ``backend/data/models/nli-deberta-v3-base/``; the label order is read
from ``config.json`` because a hardcoded order would silently swap
entailment with contradiction. When the files (or ONNX Runtime) are missing,
:meth:`OnnxNliScorer.score` raises :class:`NliModelUnavailable` instead of
returning fabricated numbers, and :func:`nli_availability` reports the reason.

The contract assumes per-sentence windows: NLI cross-encoders are trained on
sentence pairs, not 500-word passages, so scoring happens window by window and
is aggregated afterwards. :meth:`OnnxNliScorer.score_batch` exists so a passage
with several windows costs one ``session.run`` per group of 8, not one run per
window.
"""
import json
import math
import os
import re
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.stance.passages import make_windows, split_sentences

#: Empirical-assertion markers, grouped by family. Matched on lowercase text
#: with token boundaries, so Romanian inflection (``contrazice``/``contrazic``)
#: is covered by the listed stems rather than exact whole-word forms.
_MARKERS: dict[str, tuple[str, ...]] = {
    "negation": ("nu", "fara", "niciodata", "not", "no", "never", "none", "without"),
    "quantifier": (
        "toate", "toți", "majoritatea", "majoritate", "all", "most", "majority",
        "significant", "semnificativ", "peste", "under", "peste 50",
    ),
    "causal": (
        "deoarece", "determina", "determină", "produce", "conduce", "cauzeaza",
        "cauzează", "because", "therefore", "hence", "due", "causes", "leads",
        "deci", "astfel",
    ),
    "comparative": (
        "mai", "compared", "versus", "than", "higher", "lower", "greater",
        "less", "creste", "crește", "scade", "more", "fewer",
    ),
}
_MARKER_RE = {
    family: re.compile(
        r"(?<![\w])(" + "|".join(re.escape(m) for m in markers) + r")(?![\w])"
    )
    for family, markers in _MARKERS.items()
}

#: Default location of the exported cross-encoder, relative to the backend
#: package root (``app/stance/signals.py`` → ``parents[2]`` = ``backend``).
_DEFAULT_MODEL_DIR = (
    Path(__file__).resolve().parents[2] / "data" / "models" / "nli-deberta-v3-base"
)
_MODEL_FILE = "model_quantized.onnx"
_TOKENIZER_FILE = "tokenizer.json"
_CONFIG_FILE = "config.json"
_SOURCE = "onnx-nli-deberta-v3-base"
_MAX_LENGTH = 512
_BATCH_SIZE = 8

#: Loaded ONNX sessions, keyed by resolved model directory. Loading 233 MB per
#: request would be fatal, so this cache is process-wide.
_session_cache: dict[str, object] = {}
_tokenizer_cache: dict[str, object] = {}
#: ``id -> label`` maps read from each model's ``config.json``.
_labels_cache: dict[str, tuple[str, ...]] = {}
_cache_lock = threading.Lock()


class NliModelUnavailable(RuntimeError):
    """Raised when the ONNX model cannot be loaded or executed."""


@runtime_checkable
class NliScorer(Protocol):
    """Layer 1 scorer contract — one NLI decision per (claim, window) pair."""

    source: str

    def score(self, claim: str, window: str) -> dict:
        """Return ``{entailment, contradiction, neutral, source}``.

        Probabilities are floats in [0, 1] or ``None`` when unavailable.
        """
        ...


class DeterministicScorer:
    """No model, no dependencies: marker presence + length only.

    Probabilities are always ``None`` (see the module docstring). What it does
    provide is ``assertive``: does the window look like an empirical assertion
    (a negation/quantifier/causal/comparative marker *and* at least 6 tokens)?
    That is the cheap pre-filter — enough to decide which windows are worth
    sending to a real model.
    """

    source = "heuristic"

    def score(self, claim: str, window: str) -> dict:
        text = (window or "").lower()
        matched = sorted(
            family for family, pattern in _MARKER_RE.items() if pattern.search(text)
        )
        tokens = re.findall(r"\S+", window or "")
        return {
            "entailment": None,
            "contradiction": None,
            "neutral": None,
            "source": self.source,
            "assertive": bool(matched) and len(tokens) >= 6,
            "matched_markers": matched,
        }


def _resolve_model_dir(model_dir: str | Path | None = None) -> Path:
    """Argument → ``ONNX_NLI_MODEL_DIR`` env → the repo default."""
    if model_dir is not None:
        return Path(model_dir).expanduser()
    env = os.environ.get("ONNX_NLI_MODEL_DIR")
    if env:
        return Path(env).expanduser()
    return _DEFAULT_MODEL_DIR


def _model_files(model_dir: Path) -> tuple[Path, Path, Path]:
    return (
        model_dir / _MODEL_FILE,
        model_dir / _TOKENIZER_FILE,
        model_dir / _CONFIG_FILE,
    )


def _model_present(model_dir: Path) -> bool:
    return all(path.is_file() for path in _model_files(model_dir))


def _read_labels(model_dir: Path) -> tuple[str, ...]:
    """Label order from ``config.json``'s ``id2label`` (cached per directory).

    Fallback order (contradiction, entailment, neutral) matches the current
    DeBERTa-v3 export, but is only used when the config is unreadable — reading
    it is the contract, because a different export may order the logits
    differently and silently swap entailment with contradiction.
    """
    key = str(model_dir)
    with _cache_lock:
        cached = _labels_cache.get(key)
    if cached is not None:
        return cached
    labels: tuple[str, ...]
    try:
        config = json.loads((model_dir / _CONFIG_FILE).read_text(encoding="utf-8"))
        id2label = config["id2label"]
        labels = tuple(
            id2label[str(index)].lower() for index in range(len(id2label))
        )
        if set(labels) != {"entailment", "contradiction", "neutral"}:
            raise ValueError(f"unexpected id2label: {id2label}")
    except (OSError, KeyError, ValueError, TypeError):
        labels = ("contradiction", "entailment", "neutral")
    with _cache_lock:
        _labels_cache[key] = labels
    return labels


def _load_session(model_dir: Path):
    """Load (and cache) the ONNX session for ``model_dir``."""
    key = str(model_dir)
    with _cache_lock:
        cached = _session_cache.get(key)
    if cached is not None:
        return cached
    model_file, _, _ = _model_files(model_dir)
    if not _model_present(model_dir):
        raise NliModelUnavailable(
            f"Model NLI inexistent în {model_dir} (lipsesc {_MODEL_FILE}, "
            f"{_TOKENIZER_FILE} sau {_CONFIG_FILE})."
        )
    try:
        import onnxruntime  # type: ignore

        options = onnxruntime.SessionOptions()
        options.graph_optimization_level = (
            onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        )
        # Threads stay at the ORT default (the machine has 8 cores); pinning
        # intra_op to 1 would throw away most of that.
        session = onnxruntime.InferenceSession(
            str(model_file),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
    except Exception as exc:  # noqa: BLE001 — surface as a capability, not a crash
        raise NliModelUnavailable(f"ONNX Runtime nu a putut încărca modelul: {exc}") from exc
    with _cache_lock:
        _session_cache[key] = session
    return session


def _load_tokenizer(model_dir: Path):
    """Load (and cache) the ``tokenizers`` tokenizer for ``model_dir``."""
    key = str(model_dir)
    with _cache_lock:
        cached = _tokenizer_cache.get(key)
    if cached is not None:
        return cached
    tokenizer_file = model_dir / _TOKENIZER_FILE
    if not tokenizer_file.is_file():
        raise NliModelUnavailable(f"Lipsește {tokenizer_file}.")
    try:
        from tokenizers import Tokenizer  # type: ignore

        tokenizer = Tokenizer.from_file(str(tokenizer_file))
    except Exception as exc:  # noqa: BLE001
        raise NliModelUnavailable(
            f"Tokenizerul NLI nu a putut fi încărcat: {exc}"
        ) from exc
    with _cache_lock:
        _tokenizer_cache[key] = tokenizer
    return tokenizer


def _softmax(logits: list[float]) -> list[float]:
    if not logits:
        return []
    peak = max(logits)
    exps = [math.exp(value - peak) for value in logits]
    total = sum(exps)
    if total <= 0:
        return [0.0 for _ in logits]
    return [value / total for value in exps]


class OnnxNliScorer:
    """Local DeBERTa-v3 NLI cross-encoder behind the :class:`NliScorer` API.

    ``session`` and ``tokenizer`` are injectable so tests never load the
    233 MB model. When omitted they are loaded lazily from ``model_dir`` and
    cached at module level.
    """

    source = _SOURCE

    def __init__(
        self,
        model_dir: str | Path | None = None,
        *,
        session=None,
        tokenizer=None,
    ):
        self.model_dir = _resolve_model_dir(model_dir)
        self._session = session
        self._tokenizer = tokenizer

    def _ensure_loaded(self):
        if self._session is None:
            self._session = _load_session(self.model_dir)
        if self._tokenizer is None:
            self._tokenizer = _load_tokenizer(self.model_dir)
        return self._session, self._tokenizer

    def score(self, claim: str, window: str) -> dict:
        """Return softmax probabilities for one (claim, window) pair."""
        return self.score_batch([(claim or "", window or "")])[0]

    def score_batch(self, pairs: list[tuple[str, str]]) -> list[dict]:
        """Score pairs in groups of 8 with one ``session.run`` per group."""
        if not pairs:
            return []
        session, tokenizer = self._ensure_loaded()
        labels = _read_labels(self.model_dir)
        try:
            import numpy as np  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise NliModelUnavailable(f"numpy indisponibil: {exc}") from exc

        results: list[dict] = []
        for offset in range(0, len(pairs), _BATCH_SIZE):
            batch = pairs[offset:offset + _BATCH_SIZE]
            encoded = []
            for premise, hypothesis in batch:
                encoding = tokenizer.encode(premise or "", hypothesis or "")
                ids = list(encoding.ids)[:_MAX_LENGTH]
                if not ids:
                    ids = [0]
                encoded.append(ids)
            width = max(len(ids) for ids in encoded)
            input_ids = np.zeros((len(encoded), width), dtype=np.int64)
            attention_mask = np.zeros((len(encoded), width), dtype=np.int64)
            for row, ids in enumerate(encoded):
                input_ids[row, : len(ids)] = ids
                attention_mask[row, : len(ids)] = 1
            feed = {"input_ids": input_ids, "attention_mask": attention_mask}
            try:
                outputs = session.run(None, feed)
            except Exception as exc:  # noqa: BLE001
                raise NliModelUnavailable(
                    f"Inferența ONNX a eșuat: {exc}"
                ) from exc
            logits = outputs[0]
            for row in range(len(encoded)):
                probs = _softmax([float(value) for value in logits[row]])
                record = {"entailment": 0.0, "contradiction": 0.0, "neutral": 0.0}
                for index, label in enumerate(labels):
                    if index < len(probs) and label in record:
                        record[label] = probs[index]
                record["source"] = self.source
                results.append(record)
        return results


def nli_availability() -> dict:
    """Honest capability report for the ONNX NLI layer (no model loading)."""
    model_dir = _resolve_model_dir()
    if _model_present(model_dir):
        return {
            "available": True,
            "scorer": _SOURCE,
            "model_dir": str(model_dir),
            "expected": None,
        }
    return {
        "available": False,
        "reason": "no_model_configured",
        "expected": f"onnxruntime + {_MODEL_FILE}, {_TOKENIZER_FILE}, {_CONFIG_FILE} in {model_dir}",
    }


def _max_or_none(values: list) -> float | None:
    real = [v for v in values if v is not None]
    if not real:
        return None
    return max(real)


def score_passage(claim: str, passage_text: str, scorer: NliScorer | None = None) -> dict:
    """Score a passage window by window and aggregate.

    Returns ``{windows, max_entailment, max_contradiction, any_assertive,
    scorer_source}``. With null scores the maxima stay ``None`` — never 0.

    When no scorer is given, the real model is used only when its files are
    present; otherwise the deterministic pre-filter runs.
    """
    active = scorer
    if active is None:
        if nli_availability()["available"]:
            active = OnnxNliScorer()
        else:
            active = DeterministicScorer()
    windows = make_windows(split_sentences(passage_text or ""))
    scores: list[dict] = []
    batch_scorer = getattr(active, "score_batch", None)
    if callable(batch_scorer):
        scores = batch_scorer([(claim or "", window) for window in windows])
    else:
        scores = [active.score(claim or "", window) for window in windows]
    entries: list[dict] = []
    prefilter = DeterministicScorer()
    for window, score in zip(windows, scores):
        heuristic = prefilter.score(claim or "", window)
        entries.append(
            {
                "text": window,
                "entailment": score.get("entailment"),
                "contradiction": score.get("contradiction"),
                "neutral": score.get("neutral"),
                "source": score.get("source") or getattr(active, "source", ""),
                # A real NLI scorer reports probabilities only; the cheap
                # marker pre-filter still decides what counts as "assertive".
                "assertive": bool(
                    score.get("assertive", heuristic["assertive"])
                ),
                "matched_markers": list(
                    score.get("matched_markers") or heuristic["matched_markers"]
                ),
            }
        )
    return {
        "windows": entries,
        "max_entailment": _max_or_none([w["entailment"] for w in entries]),
        "max_contradiction": _max_or_none([w["contradiction"] for w in entries]),
        "any_assertive": any(w["assertive"] for w in entries),
        "scorer_source": entries[0]["source"] if entries else getattr(active, "source", ""),
    }


def is_strong_signal(signal: dict, threshold: float = 0.65) -> bool:
    """True only when a *real* probability reaches the threshold.

    Null probabilities (no NLI) are never strong. Accepts either a
    ``score_passage`` result (``max_entailment`` / ``max_contradiction``) or a
    single window score (``entailment`` / ``contradiction``).
    """
    if not isinstance(signal, dict):
        return False
    candidates = []
    for key in ("max_entailment", "max_contradiction", "entailment", "contradiction"):
        if key in signal:
            candidates.append(signal.get(key))
    return any(value is not None and value >= threshold for value in candidates)
