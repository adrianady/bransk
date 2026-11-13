"""Layer 1 ONNX NLI scorer — hermetic fakes + one guarded real-model check."""
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from app.stance.signals import (
    NliModelUnavailable,
    OnnxNliScorer,
    DeterministicScorer,
    nli_availability,
    score_passage,
)

_REAL_MODEL = nli_availability()["available"]


def _config_dir(tmp_path, id2label):
    (tmp_path / "config.json").write_text(
        json.dumps({"id2label": id2label}), encoding="utf-8"
    )
    return tmp_path


class _FakeTokenizer:
    def __init__(self, length=5):
        self.length = length
        self.calls = 0

    def encode(self, premise, hypothesis):
        self.calls += 1
        ids = [101] + [1] * (self.length - 2) + [102]
        return SimpleNamespace(ids=ids, attention_mask=[1] * len(ids))


class _FakeSession:
    """Returns fixed logits per row and counts every ``run`` call."""

    def __init__(self, logits=(3.0, 0.0, 0.0)):
        self.logits = list(logits)
        self.calls = 0
        self.last_feed = None

    def run(self, inputs, feed):
        self.calls += 1
        self.last_feed = feed
        batch = feed["input_ids"].shape[0]
        rows = np.tile(np.asarray(self.logits, dtype=np.float32), (batch, 1))
        return [rows]


def test_score_reads_label_order_from_config(tmp_path):
    # Deliberately non-default: 0=entailment, 1=neutral, 2=contradiction.
    model_dir = _config_dir(
        tmp_path,
        {"0": "entailment", "1": "neutral", "2": "contradiction"},
    )
    session = _FakeSession(logits=(3.0, 0.0, 0.0))
    scorer = OnnxNliScorer(
        model_dir, session=session, tokenizer=_FakeTokenizer()
    )

    result = scorer.score("Poverty is low income.", "Poverty is not low income.")

    assert result["source"] == "onnx-nli-deberta-v3-base"
    assert result["entailment"] == pytest.approx(0.909, abs=0.01)
    assert result["entailment"] > result["neutral"]
    assert result["neutral"] == pytest.approx(result["contradiction"])
    assert sum(result[k] for k in ("entailment", "contradiction", "neutral")) == (
        pytest.approx(1.0)
    )


def test_score_batch_uses_groups_of_eight(tmp_path):
    model_dir = _config_dir(
        tmp_path,
        {"0": "contradiction", "1": "entailment", "2": "neutral"},
    )
    session = _FakeSession()
    scorer = OnnxNliScorer(
        model_dir, session=session, tokenizer=_FakeTokenizer()
    )

    pairs = [(f"claim {i}", f"window {i}") for i in range(9)]
    results = scorer.score_batch(pairs)

    assert len(results) == 9
    assert session.calls == 2  # 8 + 1
    assert session.last_feed["input_ids"].shape[0] == 1


def test_missing_model_reports_unavailable_and_raises(tmp_path, monkeypatch):
    missing = tmp_path / "nope"
    monkeypatch.setenv("ONNX_NLI_MODEL_DIR", str(missing))

    availability = nli_availability()
    assert availability["available"] is False
    assert availability["reason"] == "no_model_configured"
    assert str(missing) in availability["expected"]

    with pytest.raises(NliModelUnavailable):
        OnnxNliScorer(missing).score("claim", "window")

    # score_passage without a scorer degrades to the deterministic pre-filter.
    passage = (
        "Datele arată că X crește semnificativ în condiția Z. "
        "A doua propoziție este și ea suficient de lungă pentru fereastră. "
        "A treia propoziție încheie pasajul acesta."
    )
    result = score_passage("X crește.", passage)
    assert result["scorer_source"] == "heuristic"
    assert result["max_entailment"] is None
    assert result["max_contradiction"] is None


class _FixedScorer:
    source = "fake"

    def __init__(self, values):
        self.values = list(values)
        self.index = 0

    def score(self, claim, window):
        entailment, contradiction, neutral = self.values[self.index]
        self.index += 1
        return {
            "entailment": entailment,
            "contradiction": contradiction,
            "neutral": neutral,
            "source": self.source,
        }


def test_score_passage_aggregates_per_window_maxima():
    passage = (
        "Prima propoziție este suficient de lungă pentru o fereastră de trei. "
        "A doua propoziție adaugă context empiric despre variabilele studiate. "
        "A treia propoziție menține fereastra peste pragul minim de caractere. "
        "A patra propoziție închide pasajul și produce o a doua fereastră."
    )
    scorer = _FixedScorer([(0.2, 0.9, 0.1), (0.8, 0.3, 0.2)])

    result = score_passage("X crește.", passage, scorer)

    assert len(result["windows"]) == 2
    assert result["max_entailment"] == pytest.approx(0.8)
    assert result["max_contradiction"] == pytest.approx(0.9)
    assert result["scorer_source"] == "fake"


@pytest.mark.skipif(not _REAL_MODEL, reason="ONNX NLI model not installed")
def test_real_model_contradiction_and_entailment():
    scorer = OnnxNliScorer()
    claim = "Poverty is simply low income."
    contradiction = (
        "Poverty is not simply low income or absence of resources."
    )
    entailment = "A mesosystem comprises the interrelations among two or more settings."

    results = scorer.score_batch(
        [(claim, contradiction), (entailment, entailment)]
    )

    label_keys = ("entailment", "contradiction", "neutral")
    assert max(label_keys, key=lambda key: results[0][key]) == "contradiction"
    assert max(label_keys, key=lambda key: results[1][key]) == "entailment"


def test_onnx_path_never_imports_torch():
    nli_availability()
    assert "torch" not in sys.modules
