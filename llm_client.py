"""Minimal Gemini client configuration. Deterministic decisions never depend on Gemini."""
from __future__ import annotations

import os

PROVIDER = "gemini"
MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")


def has_llm_configured() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def get_client():
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Gemini API anahtarı ayarlı değil. GEMINI_API_KEY değişkenini tanımlayın.")
    from google import genai
    from google.genai import types

    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=20_000))
