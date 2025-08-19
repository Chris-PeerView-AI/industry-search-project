# modules/openai_taxonomy.py
# One-shot taxonomy builder via OpenAI Responses API (no caching yet).
# Returns a raw taxonomy dict; harness should pass it through
# Phase1_rubric.process_taxonomy(...) for normalization/post-processing.

from __future__ import annotations


import os
import json
from typing import Dict, Any
from openai import OpenAI

# Default to the more economical model; caller can override.
DEFAULT_TAXONOMY_MODEL = os.getenv("OPENAI_TAXONOMY_MODEL", "gpt-5-mini")

PROMPT = (
    "You are designing an industry-agnostic taxonomy for classifying businesses.\n"
    "Return STRICT JSON with keys: primary_terms, adjacent_terms, disqualifiers, venue_terms, exemplar_brands, industry_synonyms, notes, allow_t1_requires_primary.\n\n"
    "Guidance (industry-agnostic):\n"
    "- primary_terms: explicit venue/offering phrases (e.g., 'indoor golf simulator', 'virtual golf lounge', 'coffee cafe', 'medspa clinic').\n"
    "  Avoid generic words alone ('experience', 'services', 'solutions', 'equipment', 'supplies').\n"
    "- adjacent_terms: related but non-core (lessons/training, fittings, retail accessories, rentals).\n"
    "- disqualifiers: off-target/supply chain (wholesale distributor, manufacturer, corporate HQ, equipment brand, B2B-only supplier).\n"
    "- venue_terms: distinctive place words (e.g., lounge, studio, bay, clinic, cafe).\n"
    "- industry_synonyms: short phrases ordinary people use for this industry (e.g., for coffee: ['coffee shop','café','coffeehouse']; for indoor golf: ['indoor golf','golf simulator','screen golf']).\n"
    "- allow_t1_requires_primary: boolean.\n"
    "- Keep lists 3–8 items where applicable, lowercase where possible.\n\n"
    "Industry: {industry}\n"
    "Optional focus/subtype: {focus}\n"
)




def fetch_taxonomy_openai(industry: str, focus: str | None = None, model: str | None = None, timeout: int = 30):
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    project = (os.getenv("OPENAI_PROJECT") or "").strip() or None
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    client = OpenAI(api_key=api_key, project=project)
    ...

    prompt = PROMPT.format(industry=(industry or "").strip(), focus=(focus or "—"))
    mdl = model or DEFAULT_TAXONOMY_MODEL

    try:
        resp = client.responses.create(model=mdl, input=prompt)
        text = resp.output_text or "{}"
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}
