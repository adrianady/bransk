"""Stance pipeline (slice 6): passages → signals → hardcoded aggregation.

Layers:
- ``passages``: the span-anchored evidence set (canonical page text slices).
- ``signals``: layer 1 signals (deterministic pre-filter today, ONNX NLI later).
- ``aggregate``: layer 3, pure rule tables — no model, no DB.
"""
