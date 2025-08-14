"""
TEST_Phase1_LLM.py — Wide→Narrow discovery harness with optional LLM‑driven industry profile

Complete, copy‑paste replacement file
-------------------------------------
This is a self‑contained script to *simulate* Phase‑1 discovery outside the UI/Supabase.
It pulls candidates from Google Places (Nearby; Text Search is used **only** for geocoding when you don’t pass --center),
de‑dups, fetches Place Details, optionally enriches with light website scraping, computes a transparent eligibility
score using **either** built‑in defaults **or** an LLM‑generated industry profile (via local Ollama), then prints a funnel
summary and saves a CSV.

What’s new vs earlier harness
- LLM profile now **merges** with our safe defaults instead of overwriting (keeps guardrails like smoothie/grocery denials)
- Website analyzer adds a **generic coffee** signal; strict barista/menu terms still get a higher bonus
- **Quality bump** for established cafés (rating ≥ 4.4 & reviews ≥ 150) with a website or early‑open
- Baseline dynamic thresholds expanded: **[80, 75, 70, 65, 60, 55]**

Requirements
- Python 3.10+
- pip install requests python-dotenv
- .env with: GOOGLE_PLACES_API_KEY=...
- (Optional) Local Ollama running (default model: llama3) for --enable-llm-profile

Examples
# Coffee (LLM profile + dynamic cutoff + web scrape)
python modules/TEST_Phase1_LLM.py \
  --industry "Coffee Shops" \
  --location "Memphis, Tennessee" \
  --center "35.1495,-90.0490" \
  --target 20 \
  --max-radius-km 25 \
  --breadth normal \
  --enable-llm-profile \
  --enable-web-scrape \
  --dynamic-threshold \
  --verbose

# Hair Salons (override settings inline, no LLM)
python modules/TEST_Phase1_LLM.py \
  --industry "Hair Salons" \
  --location "Austin, Texas" \
  --target 30 \
  --max-radius-km 20 \
  --breadth normal \
  --allow-types "hair_salon,beauty_salon" \
  --soft-deny-types "spa" \
  --name-positive "salon,hair,blowout,barber" \
  --include-keywords "haircut,stylist,color,barber" \
  --dynamic-threshold
"""

from __future__ import annotations

import os
import sys
import csv
import math
import time
import json
import re
import argparse
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Set, Iterable
from datetime import datetime
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

# -----------------------------
# Configuration & Data Classes
# -----------------------------

@dataclass
class IndustrySettings:
    allow_types: Set[str]
    soft_deny_types: Set[str]
    include_keywords: Set[str]
    exclude_keywords: Set[str]
    name_positive: Set[str]
    name_negative: Set[str]
    early_open_hour: Optional[int] = None  # e.g., 7 means <07:30 is a positive signal
    bakery_demote: bool = False            # coffee-specific nuance
    # LLM-driven profile fields (optional)
    weights: Dict[str, int] = field(default_factory=lambda: {
        "allow_types": 40,
        "soft_deny": -30,
        "name_pos_base": 10,
        "name_pos_step": 5,
        "name_neg_base": -10,
        "early_open_bonus": 10,
        "rating_bonus": 5,
        "website_bonus": 5,
        # Optional extras respected by scorer (safe defaults)
        "web_coffee_bonus": 10,
        "web_generic_coffee_bonus": 5,
        "schema_cafe_bonus": 5,
        "quality_bonus": 5,
        "low_quality_penalty": -15,
        "restaurant_cafe_cap": -10,
    })
    threshold_candidates: List[int] = field(default_factory=lambda: [80, 75, 70, 65, 60, 55])
    floor_ratio: float = 0.6
    category_demotions: List[Dict[str, object]] = field(default_factory=list)
    profile_source: str = "defaults"

@dataclass
class DiscoveryParams:
    breadth: str  # 'narrow' | 'normal' | 'wide'
    target_count: int
    max_radius_km: float
    grid_step_km: float = 2.5
    per_node_radius_m: Tuple[int, ...] = (1000, 2500, 5000)
    oversample_factor: int = 3

    def __post_init__(self):
        breadth_map = {"narrow": 2, "normal": 3, "wide": 4}
        if self.breadth not in breadth_map:
            raise ValueError("breadth must be one of: narrow|normal|wide")
        self.oversample_factor = breadth_map[self.breadth]

# -----------------------------
# Helpers
# -----------------------------

EARTH_RADIUS_KM = 6371.0088

PLACES_BASE = "https://maps.googleapis.com/maps/api/place"

# A small, extensible set of known Google Place types we recognize.
KNOWN_TYPES = {
    "cafe","coffee_shop","restaurant","bar","night_club","market","grocery_or_supermarket",
    "tourist_attraction","museum","zoo","point_of_interest","establishment","food","store",
    "hair_salon","beauty_salon","spa","barber_shop","pest_control","hardware_store",
    "roofing_contractor","plumber","electrician","gym","bakery","book_store","shopping_mall","supermarket"
}

def km_to_deg_lat(km: float) -> float:
    return km / 111.0

def km_to_deg_lon(km: float, lat_deg: float) -> float:
    return km / (111.320 * math.cos(math.radians(lat_deg)) or 1e-9)

def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in meters."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c * 1000

# -----------------------------
# Google Places API (v1 HTTP)
# -----------------------------

class GooglePlaces:
    def __init__(self, api_key: str, rate_delay: float = 0.05):
        self.key = api_key
        self.delay = rate_delay
        self.session = requests.Session()

    def _get(self, path: str, params: Dict) -> Dict:
        params = {**params, "key": self.key}
        url = f"{PLACES_BASE}/{path}/json?{urlencode(params)}"
        resp = self.session.get(url, timeout=20)
        time.sleep(self.delay)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "")
        if status not in ("OK", "ZERO_RESULTS"):
            msg = data.get("error_message")
            raise RuntimeError(f"Places API {path} status={status} error={msg}")
        return data

    def text_search_geocode(self, query: str) -> Tuple[float, float]:
        data = self._get("textsearch", {"query": query})
        results = data.get("results", [])
        if not results:
            raise ValueError(f"No geocode result for '{query}'")
        best = None
        for r in results:
            types = set(r.get("types", []))
            if {"locality", "political"}.intersection(types):
                best = r
                break
        if not best:
            best = results[0]
        loc = best["geometry"]["location"]
        return float(loc["lat"]), float(loc["lng"])

    def nearby_search(self, lat: float, lng: float, radius_m: int, keyword: Optional[str] = None, type_hint: Optional[str] = None, pagetoken: Optional[str] = None) -> Dict:
        params = {"location": f"{lat},{lng}", "radius": radius_m}
        if keyword:
            params["keyword"] = keyword
        if type_hint:
            params["type"] = type_hint
        if pagetoken:
            params = {"pagetoken": pagetoken}
        return self._get("nearbysearch", params)

    def place_details(self, place_id: str) -> Dict:
        fields = [
            "name",
            "formatted_address",
            "geometry/location",
            "types",
            "website",
            "url",
            "opening_hours",
            "rating",
            "user_ratings_total",
            "business_status",
        ]
        params = {"place_id": place_id, "fields": ",".join(fields)}
        return self._get("details", params)

    def text_search(self, query: str, location_bias: Optional[Tuple[float,float]] = None, radius_m: Optional[int] = None) -> Dict:
        params = {"query": query}
        if location_bias and radius_m:
            params["location"] = f"{location_bias[0]},{location_bias[1]}"
            params["radius"] = radius_m
        return self._get("textsearch", params)

# -----------------------------
# Web signals & brand helpers
# -----------------------------
CHAIN_BRANDS = {
    "starbucks": "Starbucks",
    "dunkin": "Dunkin'",
    "scooter": "Scooter's",
    "7 brew": "7 Brew",
    "7brew": "7 Brew",
    "peet": "Peet's",
    "tim horton": "Tim Hortons",
    "caribou": "Caribou Coffee",
    "blue bottle": "Blue Bottle",
    "philz": "Philz Coffee",
    "dutch bros": "Dutch Bros",
    "biggby": "Biggby Coffee",
}

COFFEE_PAGE_TERMS_RE = re.compile(
    r"\b(espresso|latte|cappuccino|americano|pour\s*over|pourover|single\s*origin|"
    r"roast(er|ery)?|barista|cold\s*brew|drip)\b", re.I
)
SCHEMA_CAFE_RE = re.compile(r"@type\"?\s*:\s*\"?(CafeOrCoffeeShop|Cafe|CoffeeShop)\"?", re.I)
GENERIC_COFFEE_RE = re.compile(r"\b(coffee|coffeehouse|coffee\s*bar)\b", re.I)

def detect_chain_brand(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    n = name.lower()
    for key, label in CHAIN_BRANDS.items():
        if key in n:
            return label
    return None

def fetch_site_text(url: str, timeout: float = 8.0, max_bytes: int = 200_000) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118 Safari/537.36"}
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        ct = (r.headers.get("content-type") or "").lower()
        if ("html" not in ct) and ("text" not in ct):
            return ""
        text = r.text or ""
        if len(text) > max_bytes:
            text = text[:max_bytes]
        text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
        text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        return text
    except Exception:
        return ""

def analyze_website_text(text: str) -> Dict[str, bool]:
    if not text:
        return {"has_coffee_terms": False, "has_generic_coffee": False, "has_schema_cafe": False}
    return {
        "has_coffee_terms": bool(COFFEE_PAGE_TERMS_RE.search(text)),
        "has_generic_coffee": bool(GENERIC_COFFEE_RE.search(text)),
        "has_schema_cafe": bool(SCHEMA_CAFE_RE.search(text)),
    }

# -----------------------------
# Settings helpers (industry‑agnostic tuning)
# -----------------------------

def parse_csv_set(s: Optional[str]) -> Optional[Set[str]]:
    if s is None:
        return None
    # Support empty string override → empty set
    if s == "":
        return set()
    return {tok.strip().lower() for tok in s.split(',') if tok and tok.strip()}


def apply_industry_overrides(settings: IndustrySettings, args: argparse.Namespace) -> IndustrySettings:
    """Apply settings from a JSON file and/or CLI CSV overrides."""
    data: Dict = {}
    path = getattr(args, 'settings_file', None)
    if path:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f) or {}
        except Exception as e:
            logging.warning(f"Failed to load --settings-file='{path}': {e}")
            data = {}

    def pick_set(default: Set[str], file_key: str, cli_val: Optional[str]) -> Set[str]:
        cli_set = parse_csv_set(cli_val)
        if cli_set is not None:
            return cli_set
        if file_key in data and isinstance(data[file_key], (list, set, tuple)):
            return {str(x).lower() for x in data[file_key]}
        return default

    # Set-like fields
    settings.allow_types = pick_set(settings.allow_types, 'allow_types', getattr(args, 'allow_types', None))
    settings.soft_deny_types = pick_set(settings.soft_deny_types, 'soft_deny_types', getattr(args, 'soft_deny_types', None))
    settings.include_keywords = pick_set(settings.include_keywords, 'include_keywords', getattr(args, 'include_keywords', None))
    settings.exclude_keywords = pick_set(settings.exclude_keywords, 'exclude_keywords', getattr(args, 'exclude_keywords', None))
    settings.name_positive = pick_set(settings.name_positive, 'name_positive', getattr(args, 'name_positive', None))
    settings.name_negative = pick_set(settings.name_negative, 'name_negative', getattr(args, 'name_negative', None))

    # Scalar fields
    if getattr(args, 'early_open_hour', None) is not None:
        settings.early_open_hour = args.early_open_hour
    elif 'early_open_hour' in data:
        try:
            settings.early_open_hour = int(data['early_open_hour'])
        except Exception:
            pass

    if getattr(args, 'bakery_demote', None) is not None:
        settings.bakery_demote = str(args.bakery_demote).lower() == 'true'
    elif 'bakery_demote' in data:
        settings.bakery_demote = bool(data['bakery_demote'])

    return settings


def assign_predicted_tier(score: int, tier1_threshold: int) -> int:
    if score >= tier1_threshold:
        return 1
    elif score >= 50:
        return 2
    return 3


def choose_tier1_threshold(candidates: Iterable[Dict], target: int, floor_ratio: float, thresholds: List[int]) -> int:
    """Pick the highest threshold that still yields enough Tier‑1s.
    Enough = at least min(target, floor_ratio * likely_eligible), where likely_eligible = score>=50.
    If none match, return the threshold that yields the most Tier‑1s (tie -> lower threshold).
    """
    scores = [int(c.get('eligibility_score', 0)) for c in candidates]
    likely_eligible = sum(1 for s in scores if s >= 50)
    required = max(1, min(target, int(round(floor_ratio * max(0, likely_eligible)))))

    best_t = thresholds[-1] if thresholds else 65
    best_count = -1
    for t in sorted(set(thresholds), reverse=True):
        count = sum(1 for s in scores if s >= t)
        if count >= required:
            return t
        if count > best_count or (count == best_count and t < best_t):
            best_t = t
            best_count = count
    return best_t

# ---- LLM‑driven profile synthesis (optional) ----

def _top_types_summary(types_freq: Dict[str, int], top_n: int = 12) -> List[Tuple[str, int]]:
    return sorted(types_freq.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]


def seed_types_frequency(gp: GooglePlaces, lat0: float, lon0: float, industry: str) -> Dict[str, int]:
    """Lightweight seed pull to observe local types; minimizes quota.
    Strategy: two radii (800m, 1500m) around center with keyword=industry.
    """
    freq: Dict[str, int] = {}
    for rad in (800, 1500):
        try:
            data = gp.nearby_search(lat0, lon0, rad, keyword=industry)
        except Exception:
            continue
        for r in data.get("results", []):
            for t in r.get("types", []) or []:
                t = str(t).lower()
                freq[t] = freq.get(t, 0) + 1
    return freq


def build_profile_prompt(industry: str, location: str, types_freq: Dict[str, int], known_types: Set[str]) -> str:
    top_types = _top_types_summary(types_freq)
    top_types_str = "\n".join([f"- {t}: {n}" for t, n in top_types]) or "(none)"
    allowed_types = ", ".join(sorted(known_types))
    schema = (
        "{\n"
        "  \"allow_types\": [\"cafe\"],\n"
        "  \"soft_deny_types\": [\"restaurant\"],\n"
        "  \"name_positive\": [\"coffee\", \"espresso\"],\n"
        "  \"name_negative\": [\"market\"],\n"
        "  \"include_keywords\": [\"espresso\"],\n"
        "  \"exclude_keywords\": [\"banquet\"],\n"
        "  \"early_open_hour\": 7,\n"
        "  \"category_demotions\": [{\"type\": \"bakery\", \"delta\": -20}],\n"
        "  \"weights\": {\n"
        "    \"allow_types\": 40, \"soft_deny\": -30, \"name_pos_base\": 10, \"name_pos_step\": 5, \"name_neg_base\": -10, \"early_open_bonus\": 10, \"rating_bonus\": 5, \"website_bonus\": 5\n"
        "  },\n"
        "  \"threshold_candidates\": [75,70,65,60],\n"
        "  \"floor_ratio\": 0.6\n"
        "}"
    )
    prompt = f"""
You are generating an industry discovery profile for Google Places scoring.
Industry: {industry}
Location: {location}
Observed local Google types (approximate):\n{top_types_str}

Choose only from this allowed Google types list:\n{allowed_types}

Return STRICT JSON (no prose) that conforms exactly to this schema (values may change, structure must not):\n{schema}

Constraints:\n- Pick allow_types/soft_deny_types only from the allowed list above.\n- Weights must stay within: allow_types [20..50], soft_deny [-40..-10], name_pos_base [5..15], name_pos_step [3..7], name_neg_base [-15..-5], early_open_bonus [0..15], rating_bonus [0..10], website_bonus [0..10].\n- early_open_hour: integer or null; if N/A for this industry, use null.\n- category_demotions: optional; each item has a type (from allowed list) and delta in [-50..0].\n- threshold_candidates: descending ints between 50 and 90; floor_ratio in [0.3..0.9].\n"""
    return prompt


def parse_json_loose(text: str) -> Optional[Dict]:
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def ollama_generate_profile_json(model: str, url_base: str, prompt: str, temperature: float = 0.2, timeout: int = 60) -> Optional[Dict]:
    try:
        resp = requests.post(f"{url_base.rstrip('/')}/api/generate", json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "temperature": max(0.0, min(1.0, float(temperature)))
        }, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        text = data.get("response") or ""
        return parse_json_loose(text)
    except Exception as e:
        logging.warning(f"Ollama profile generation failed: {e}")
        return None


def validate_profile_json(profile: Dict, allowed_types: Set[str]) -> Dict:
    if not isinstance(profile, dict):
        return {}
    out: Dict[str, object] = {}
    def _as_list_str(key: str) -> List[str]:
        vals = profile.get(key) or []
        if not isinstance(vals, list):
            return []
        res = []
        for v in vals:
            s = str(v).strip().lower()
            if key.endswith("types"):
                if s in allowed_types:
                    res.append(s)
            else:
                if s:
                    res.append(s)
        return list(dict.fromkeys(res))[:20]

    out["allow_types"] = _as_list_str("allow_types")
    out["soft_deny_types"] = _as_list_str("soft_deny_types")
    out["name_positive"] = _as_list_str("name_positive")
    out["name_negative"] = _as_list_str("name_negative")
    out["include_keywords"] = _as_list_str("include_keywords")
    out["exclude_keywords"] = _as_list_str("exclude_keywords")

    # Scalars
    eh = profile.get("early_open_hour")
    try:
        eh_i = int(eh) if eh is not None else None
    except Exception:
        eh_i = None
    if eh_i is not None and not (5 <= eh_i <= 10):
        eh_i = None
    out["early_open_hour"] = eh_i

    # Category demotions
    cds = []
    for item in profile.get("category_demotions") or []:
        try:
            t = str(item.get("type")).lower()
            d = int(item.get("delta"))
            if t in allowed_types and -50 <= d <= 0:
                cds.append({"type": t, "delta": d})
        except Exception:
            continue
    out["category_demotions"] = cds

    # Weights
    w = profile.get("weights") or {}
    def clamp(v, lo, hi):
        try:
            x = float(v)
        except Exception:
            return None
        return int(min(hi, max(lo, x)))
    weights = {
        "allow_types": clamp(w.get("allow_types"), 20, 50) or 40,
        "soft_deny": clamp(w.get("soft_deny"), -40, -10) or -30,
        "name_pos_base": clamp(w.get("name_pos_base"), 5, 15) or 10,
        "name_pos_step": clamp(w.get("name_pos_step"), 3, 7) or 5,
        "name_neg_base": clamp(w.get("name_neg_base"), -15, -5) or -10,
        "early_open_bonus": clamp(w.get("early_open_bonus"), 0, 15) or 10,
        "rating_bonus": clamp(w.get("rating_bonus"), 0, 10) or 5,
        "website_bonus": clamp(w.get("website_bonus"), 0, 10) or 5,
        # extras kept within reasonable bounds
        "web_coffee_bonus": 10,
        "web_generic_coffee_bonus": 5,
        "schema_cafe_bonus": 5,
        "quality_bonus": 5,
        "low_quality_penalty": -15,
        "restaurant_cafe_cap": -10,
    }
    out["weights"] = weights

    # Thresholds & ratio
    ths = []
    for t in profile.get("threshold_candidates") or []:
        try:
            ti = int(t)
            if 50 <= ti <= 90:
                ths.append(ti)
        except Exception:
            continue
    ths = sorted(list(dict.fromkeys(ths)) or [75, 70, 65, 60], reverse=True)
    out["threshold_candidates"] = ths

    fr = profile.get("floor_ratio")
    try:
        frf = float(fr)
    except Exception:
        frf = 0.6
    if not (0.3 <= frf <= 0.9):
        frf = 0.6
    out["floor_ratio"] = frf

    return out

# --- PROFILE MERGE (LLM + defaults) ------------------------------------------

def _merge_set(a: Set[str] | None, b: Iterable[str] | None) -> Set[str]:
    left = {str(x).strip().lower() for x in (a or set()) if str(x).strip()}
    right = {str(x).strip().lower() for x in (b or []) if str(x).strip()}
    return left | right


def apply_profile_to_settings(base: IndustrySettings, prof: Dict) -> IndustrySettings:
    """Merge the LLM profile into existing defaults instead of overwriting."""
    base.allow_types = _merge_set(base.allow_types, prof.get("allow_types"))
    base.soft_deny_types = _merge_set(base.soft_deny_types, prof.get("soft_deny_types"))
    base.name_positive = _merge_set(base.name_positive, prof.get("name_positive"))
    base.name_negative = _merge_set(base.name_negative, prof.get("name_negative"))
    base.include_keywords = _merge_set(base.include_keywords, prof.get("include_keywords"))
    base.exclude_keywords = _merge_set(base.exclude_keywords, prof.get("exclude_keywords"))

    eh = prof.get("early_open_hour")
    if base.early_open_hour is None and isinstance(eh, int):
        base.early_open_hour = eh

    cds = {d["type"]: d for d in (base.category_demotions or []) if isinstance(d, dict) and d.get("type")}
    for item in prof.get("category_demotions") or []:
        try:
            t = str(item.get("type")).lower()
            delta = int(item.get("delta"))
        except Exception:
            continue
        if t not in cds or delta < int(cds[t].get("delta", 0)):
            cds[t] = {"type": t, "delta": delta}
    base.category_demotions = list(cds.values())

    w = dict(base.weights or {})
    for k, v in (prof.get("weights") or {}).items():
        try:
            w[k] = int(v)
        except Exception:
            continue
    base.weights = w

    th = sorted({*base.threshold_candidates, *(prof.get("threshold_candidates") or [])}, reverse=True)
    base.threshold_candidates = [int(x) for x in th]
    if isinstance(prof.get("floor_ratio"), (int, float)):
        base.floor_ratio = float(prof["floor_ratio"])

    base.profile_source = "llm"
    return base

# -----------------------------
# Discovery & Scoring
# -----------------------------

def build_industry_settings(industry: str) -> IndustrySettings:
    s = industry.strip().lower()
    if "coffee" in s:
        return IndustrySettings(
            allow_types={"cafe", "coffee_shop"},
            soft_deny_types={"restaurant", "bar", "night_club", "market", "grocery_or_supermarket", "tourist_attraction", "museum", "zoo"},
            include_keywords={"coffee", "espresso", "latte", "cappuccino", "americano", "roaster", "pour over", "drip", "cold brew"},
            exclude_keywords={"banquet", "nightclub", "wedding", "steakhouse", "seafood"},
            name_positive={"coffee", "espresso", "brew", "roast", "roasters", "cafe"},
            name_negative={"market", "grill", "palace", "lounge", "club", "eatery", "smoothie", "nutrition", "casino", "zoo", "museum"},
            early_open_hour=7,
            bakery_demote=True,
        )
    # Generic fallback
    return IndustrySettings(
        allow_types=set(),
        soft_deny_types={"tourist_attraction"},
        include_keywords=set(),
        exclude_keywords=set(),
        name_positive=set(),
        name_negative=set(),
        early_open_hour=None,
        bakery_demote=False,
    )


def generate_grid(lat0: float, lon0: float, max_radius_km: float, step_km: float) -> List[Tuple[float, float, float]]:
    pts: List[Tuple[float, float, float]] = [(lat0, lon0, 0.0)]
    r = step_km
    while r <= max_radius_km + 1e-9:
        n = max(6, int(math.ceil((2 * math.pi * r) / step_km)))
        for k in range(n):
            theta = (2 * math.pi) * (k / n)
            dlat = km_to_deg_lat(r * math.sin(theta))
            dlon = km_to_deg_lon(r * math.cos(theta), lat0)
            pts.append((lat0 + dlat, lon0 + dlon, r))
        r += step_km
    return pts


def soft_dedup_by_name_and_distance(cands: Dict[str, Dict], threshold_m: int = 150) -> Dict[str, Dict]:
    buckets: Dict[str, List[Tuple[str, Dict]]] = {}
    for pid, c in cands.items():
        name_key = (c.get("name") or "").strip().lower()
        buckets.setdefault(name_key, []).append((pid, c))

    keep: Dict[str, Dict] = {}
    for name_key, items in buckets.items():
        used = set()
        for i, (pid_i, ci) in enumerate(items):
            if pid_i in used:
                continue
            keep[pid_i] = ci
            used.add(pid_i)
            for j in range(i + 1, len(items)):
                pid_j, cj = items[j]
                if pid_j in used:
                    continue
                d = haversine_m(ci["lat"], ci["lng"], cj["lat"], cj["lng"])
                if d <= threshold_m:
                    used.add(pid_j)
    return keep


def earliest_open_hour(opening_hours: Optional[Dict]) -> Optional[float]:
    if not opening_hours:
        return None
    periods = opening_hours.get("periods") or []
    earliest = None
    for p in periods:
        open_info = p.get("open") or {}
        time_str = open_info.get("time")
        if not time_str or len(time_str) < 3:
            continue
        hh = int(time_str[:2])
        mm = int(time_str[2:]) if len(time_str) >= 4 else 0
        h = hh + mm / 60.0
        if earliest is None or h < earliest:
            earliest = h
    return earliest


def score_candidate(c: Dict, s: IndustrySettings) -> Tuple[int, List[str]]:
    score = 0
    reasons: List[str] = []

    types = set(c.get("types", []))
    name = (c.get("name") or "").lower()
    w = s.weights or {}

    # Type-based
    allow_hit = s.allow_types.intersection(types) if s.allow_types else set()
    deny_hit = s.soft_deny_types.intersection(types) if s.soft_deny_types else set()
    if allow_hit:
        add = int(w.get("allow_types", 40)); score += add; reasons.append(f"allow_types(+{add}): {allow_hit}")
    if deny_hit:
        sub = int(w.get("soft_deny", -30)); score += sub; reasons.append(f"soft_deny({sub}): {deny_hit}")
        # Cap restaurant penalty when also a cafe
        if ("cafe" in types or "coffee_shop" in types) and ("restaurant" in deny_hit):
            cap = int(w.get("restaurant_cafe_cap", -10))
            if sub < cap:
                adjust = cap - sub
                score += adjust; reasons.append(f"cafe_overrides_restaurant(+{adjust})")

    # Name tokens
    pos = sum(1 for tok in s.name_positive if tok in name)
    neg = sum(1 for tok in s.name_negative if tok in name)
    if pos:
        base = int(w.get("name_pos_base", 10)); step = int(w.get("name_pos_step", 5))
        add = min(20, base + step * (pos - 1))
        score += add; reasons.append(f"name_pos(+{add}): {pos} hit(s)")
    if neg:
        base = int(w.get("name_neg_base", -10))
        sub = max(-20, base - 5 * (neg - 1))
        score += sub; reasons.append(f"name_neg({sub}): {neg} hit(s)")

    # Hours
    eh = earliest_open_hour(c.get("opening_hours"))
    if s.early_open_hour is not None and eh is not None:
        if eh <= (s.early_open_hour + 0.5):
            add = int(w.get("early_open_bonus", 10)); score += add; reasons.append(f"early_open(+{add}): {eh:0.2f}h")
        else:
            reasons.append(f"late_open: {eh:0.2f}h")

    # Ratings
    rating = c.get("rating")
    reviews = c.get("user_ratings_total") or 0
    if rating and reviews and rating >= 3.8 and reviews >= 25:
        add = int(w.get("rating_bonus", 5)); score += add; reasons.append(f"rating(+{add}): {rating} ({reviews})")

    # Low-quality guardrail
    low_pen = int(w.get("low_quality_penalty", -15))
    if (reviews and reviews < 10 and (not rating or rating < 3.8)) or (not reviews) or (rating and rating < 3.2):
        score += low_pen; reasons.append(f"low_quality({low_pen})")

    # Website + web signals
    if c.get("website"):
        add = int(w.get("website_bonus", 5)); score += add; reasons.append("website(+{add})".format(add=add))
    ws = c.get("web_signals") or {}
    if ws.get("has_coffee_terms"):
        add = int(w.get("web_coffee_bonus", 10)); score += add; reasons.append(f"web_terms(+{add})")
    elif ws.get("has_generic_coffee"):
        add = int(w.get("web_generic_coffee_bonus", 5)); score += add; reasons.append(f"web_generic(+{add})")
    if ws.get("has_schema_cafe"):
        add = int(w.get("schema_cafe_bonus", 5)); score += add; reasons.append(f"schema_cafe(+{add})")

    # Quality bump for established cafés
    if rating and reviews and rating >= 4.4 and reviews >= 150 and (c.get("website") or (s.early_open_hour is not None and eh is not None and eh <= (s.early_open_hour + 0.5))):
        add = int(w.get("quality_bonus", 5)); score += add; reasons.append(f"quality(+{add})")

    # Category demotions (industry-configured)
    for cd in (s.category_demotions or []):
        t = cd.get("type"); delta = int(cd.get("delta", 0))
        if t in types and delta <= 0:
            score += delta; reasons.append(f"demotion({delta}): {t}")

    # Bakery nuance (softer when also 'cafe')
    if s.bakery_demote and not any((cd.get("type") == "bakery") for cd in (s.category_demotions or [])) and "bakery" in types:
        delta = -10 if ("cafe" in types or "coffee_shop" in types) else -20
        score += delta; reasons.append(f"bakery({delta})")

    # Brand tag (no score impact)
    if c.get("brand"):
        reasons.append(f"brand:{c.get('brand')}")

    score = max(0, min(100, score))
    return score, reasons

# -----------------------------
# Orchestration
# -----------------------------

def discover_candidates(
    gp: GooglePlaces,
    industry: str,
    location: str,
    params: DiscoveryParams,
    settings: IndustrySettings,
    center: Optional[Tuple[float, float]] = None,
    enable_web_scrape: bool = False,
    web_timeout: float = 8.0,
    web_max_bytes: int = 200_000,
) -> Tuple[Dict[str, Dict], Dict[str, int]]:

    if center:
        lat0, lon0 = center
    else:
        lat0, lon0 = gp.text_search_geocode(location)

    # Build keyword & type hint (optional)
    base_keywords = sorted({k for k in settings.include_keywords if len(k) <= 20})
    keyword_str = " ".join(base_keywords) if base_keywords else None
    type_hint = None
    if "coffee" in industry.lower():
        type_hint = "cafe"

    targets_needed = params.target_count * params.oversample_factor

    found_raw: Dict[str, Dict] = {}
    grid = generate_grid(lat0, lon0, params.max_radius_km, params.grid_step_km)

    for (lat, lon, rkm) in grid:
        for rad in params.per_node_radius_m:
            data = gp.nearby_search(lat, lon, rad, keyword=keyword_str, type_hint=type_hint)
            for r in data.get("results", []):
                pid = r.get("place_id")
                if not pid or pid in found_raw:
                    continue
                found_raw[pid] = {
                    "place_id": pid,
                    "name": r.get("name"),
                    "lat": r.get("geometry", {}).get("location", {}).get("lat"),
                    "lng": r.get("geometry", {}).get("location", {}).get("lng"),
                    "types": r.get("types", []) or [],
                }
            # Pagination (up to 2 more pages)
            pagetoken = data.get("next_page_token")
            hop = 0
            while pagetoken and hop < 2:
                time.sleep(1.8)
                data = gp.nearby_search(lat, lon, rad, pagetoken=pagetoken)
                for r in data.get("results", []):
                    pid = r.get("place_id")
                    if not pid or pid in found_raw:
                        continue
                    found_raw[pid] = {
                        "place_id": pid,
                        "name": r.get("name"),
                        "lat": r.get("geometry", {}).get("location", {}).get("lat"),
                        "lng": r.get("geometry", {}).get("location", {}).get("lng"),
                        "types": r.get("types", []) or [],
                    }
                pagetoken = data.get("next_page_token")
                hop += 1

            if len(found_raw) >= targets_needed:
                break
        if len(found_raw) >= targets_needed:
            break

    # Fetch details for each unique place
    enriched: Dict[str, Dict] = {}
    for i, pid in enumerate(found_raw.keys(), start=1):
        try:
            d = gp.place_details(pid)
            res = d.get("result", {})
        except Exception as e:
            logging.warning(f"details failed for {pid}: {e}")
            base = found_raw.get(pid, {})
            if base:
                enriched[pid] = {
                    "place_id": pid,
                    "name": base.get("name"),
                    "address": None,
                    "lat": base.get("lat"),
                    "lng": base.get("lng"),
                    "types": base.get("types") or [],
                    "website": None,
                    "url": None,
                    "opening_hours": None,
                    "rating": None,
                    "user_ratings_total": None,
                    "business_status": None,
                }
            continue
        loc = res.get("geometry", {}).get("location", {})
        enriched[pid] = {
            "place_id": pid,
            "name": res.get("name"),
            "address": res.get("formatted_address"),
            "lat": loc.get("lat"),
            "lng": loc.get("lng"),
            "types": res.get("types", []) or [],
            "website": res.get("website"),
            "url": res.get("url"),
            "opening_hours": res.get("opening_hours"),
            "rating": res.get("rating"),
            "user_ratings_total": res.get("user_ratings_total"),
            "business_status": res.get("business_status"),
        }
        if i % 25 == 0:
            logging.info(f"Fetched details: {i}/{len(found_raw)}")

    # Soft de-dup by name within 150m
    dedup = soft_dedup_by_name_and_distance(enriched, threshold_m=150)

    # Optional: website scraping for web signals + brand detection
    if enable_web_scrape:
        count = 0
        for c in dedup.values():
            if c.get("website"):
                text = fetch_site_text(c["website"], timeout=web_timeout, max_bytes=web_max_bytes)
                c["web_signals"] = analyze_website_text(text)
            b = detect_chain_brand(c.get("name"))
            if b:
                c["brand"] = b
            count += 1
            if count % 20 == 0:
                logging.info(f"Web enriched: {count}/{len(dedup)}")
    else:
        for c in dedup.values():
            b = detect_chain_brand(c.get("name"))
            if b:
                c["brand"] = b

    # Score & predict tier (initial; final threshold may be dynamic later)
    for pid, c in dedup.items():
        sc, reasons = score_candidate(c, settings)
        c["eligibility_score"] = sc
        c["score_reasons"] = reasons

    # Funnel counts (predicted_tier computed later once threshold is chosen)
    funnel = {
        "found_raw": len(found_raw),
        "deduped": len(dedup),
        "likely_tier_eligible": sum(1 for c in dedup.values() if c.get("eligibility_score", 0) >= 50),
        "predicted_tier1": 0,
    }

    return dedup, funnel

# -----------------------------
# CLI / Main
# -----------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Wide→Narrow discovery harness (read‑only)")
    p.add_argument("--industry", required=True, help="e.g., 'Coffee Shops'")
    p.add_argument("--location", required=True, help="e.g., 'Memphis, Tennessee'")
    p.add_argument("--target", type=int, default=20, help="Target count (used for oversample stop)")
    p.add_argument("--max-radius-km", type=float, default=25.0, dest="max_radius_km")
    p.add_argument("--breadth", choices=["narrow", "normal", "wide"], default="normal")
    p.add_argument("--grid-step-km", type=float, default=2.5)
    p.add_argument("--rate-delay", type=float, default=0.05, help="Seconds between requests")
    p.add_argument("--verbose", action="store_true")

    # Tiering controls
    p.add_argument("--tier1-threshold", type=int, default=65, help="Fixed Tier‑1 score cutoff (ignored if --dynamic-threshold)")
    p.add_argument("--dynamic-threshold", action="store_true", help="Enable dynamic Tier‑1 cutoff selection (tries --threshold-candidates)")
    p.add_argument("--threshold-candidates", default="80,75,70,65,60,55", help="Comma list of Tier‑1 thresholds to try (high→low)")
    p.add_argument("--target-floor-ratio", type=float, default=0.6, help="Min Tier‑1 as fraction of likely-eligible (score>=50)")

    # Industry settings (file + CLI overrides)
    p.add_argument("--settings-file", help="Path to JSON with IndustrySettings fields")
    p.add_argument("--allow-types", help="CSV of Google types to allow")
    p.add_argument("--soft-deny-types", help="CSV of Google types to soft-deny")
    p.add_argument("--include-keywords", help="CSV of positive keywords (empty string disables)")
    p.add_argument("--exclude-keywords", help="CSV of negative keywords")
    p.add_argument("--name-positive", help="CSV of positive name tokens")
    p.add_argument("--name-negative", help="CSV of negative name tokens")
    p.add_argument("--early-open-hour", type=int, help="Hour for early-open positive (e.g., 7)")
    p.add_argument("--bakery-demote", choices=["true", "false"], help="Coffee-specific demotion toggle")

    # LLM profile synthesis
    p.add_argument("--enable-llm-profile", action="store_true", help="Use local LLM (Ollama) to auto-synthesize industry settings")
    p.add_argument("--ollama-model", default="llama3", help="Ollama model name (e.g., llama3)")
    p.add_argument("--ollama-url", default="http://localhost:11434", help="Base URL for Ollama server")
    p.add_argument("--llm-temp", type=float, default=0.2, help="LLM temperature (0..1)")

    # Web scraping enrichment
    p.add_argument("--enable-web-scrape", action="store_true", help="Fetch website content and apply web signals (menu terms, schema)")
    p.add_argument("--web-timeout", type=float, default=8.0, help="Per-site timeout in seconds")
    p.add_argument("--web-max-bytes", type=int, default=200000, help="Max bytes to read per page")

    # Geocode override (avoid Text Search quota)
    p.add_argument("--center", help='Override geocode as "lat,lng" (e.g., "35.1495,-90.0490")')

    return p.parse_args()


def main():
    load_dotenv()
    api_key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not api_key:
        print("Missing GOOGLE_PLACES_API_KEY in .env")
        sys.exit(2)

    args = parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s: %(message)s")

    settings = build_industry_settings(args.industry)
    settings = apply_industry_overrides(settings, args)

    gp = GooglePlaces(api_key=api_key, rate_delay=args.rate_delay)

    # Parse optional center override
    center = None
    if args.center:
        try:
            lat_str, lon_str = [x.strip() for x in args.center.split(',')]
            center = (float(lat_str), float(lon_str))
        except Exception:
            logging.warning("--center parse failed; falling back to geocode")
            center = None

    # Optional: synthesize an industry profile via local LLM (Ollama)
    if args.enable_llm_profile:
        try:
            if not center:
                center = gp.text_search_geocode(args.location)
            types_freq = seed_types_frequency(gp, center[0], center[1], args.industry)
            allowed_types = set(KNOWN_TYPES) | set(types_freq.keys())
            prompt = build_profile_prompt(args.industry, args.location, types_freq, allowed_types)
            prof_raw = ollama_generate_profile_json(args.ollama_model, args.ollama_url, prompt, temperature=args.llm_temp)
            prof = validate_profile_json(prof_raw or {}, allowed_types)
            if prof:
                settings = apply_profile_to_settings(settings, prof)
                # Re-apply CLI overrides so user preferences win over LLM profile
                settings = apply_industry_overrides(settings, args)
        except Exception as e:
            logging.warning(f"LLM profile generation skipped due to error: {e}")

    params = DiscoveryParams(
        breadth=args.breadth,
        target_count=args.target,
        max_radius_km=args.max_radius_km,
        grid_step_km=args.grid_step_km,
    )

    print(f"\n🚀 Discovery test — {args.industry} @ {args.location}")
    print(f"   breadth={args.breadth} (oversample≈{params.oversample_factor}×), max_radius_km={args.max_radius_km}, grid_step_km={args.grid_step_km}")
    print(f"   profile_source={getattr(settings, 'profile_source', 'defaults')}\n")
    if args.verbose:
        print("Profile preview:")
        print(f"   allow_types={sorted(list(settings.allow_types))}")
        print(f"   soft_deny_types={sorted(list(settings.soft_deny_types))}")
        print(f"   early_open_hour={settings.early_open_hour}")
        print(f"   weights={settings.weights}")
        print(f"   thresholds={settings.threshold_candidates} floor_ratio={settings.floor_ratio}\n")

    try:
        results, funnel = discover_candidates(
            gp, args.industry, args.location, params, settings,
            center=center,
            enable_web_scrape=args.enable_web_scrape,
            web_timeout=args.web_timeout,
            web_max_bytes=args.web_max_bytes,
        )

    except Exception as e:
        print(f"❌ Discovery failed: {e}")
        sys.exit(1)

    # --- Dynamic Tier‑1 cutoff ---
    baseline_thresholds = [80, 75, 70, 65, 60, 55]
    cli_thresholds = [int(x.strip()) for x in (args.threshold_candidates or "").split(',') if x.strip().isdigit()]
    llm_thresholds = settings.threshold_candidates if (args.enable_llm_profile and settings.threshold_candidates) else []
    thresholds = sorted(list({*baseline_thresholds, *cli_thresholds, *llm_thresholds}), reverse=True)
    chosen_threshold = args.tier1_threshold
    if args.dynamic_threshold and thresholds:
        floor = settings.floor_ratio if args.enable_llm_profile else args.target_floor_ratio
        chosen_threshold = choose_tier1_threshold(results.values(), args.target, floor, thresholds)

    # Assign predicted tiers with the chosen threshold
    for c in results.values():
        sc = int(c.get("eligibility_score", 0))
        c["predicted_tier"] = assign_predicted_tier(sc, chosen_threshold)

    # Recompute funnel with the chosen threshold
    funnel["likely_tier_eligible"] = sum(1 for c in results.values() if c.get("predicted_tier", 3) <= 2)
    funnel["predicted_tier1"] = sum(1 for c in results.values() if c.get("predicted_tier") == 1)

    # Sort by eligibility score desc
    rows = sorted(results.values(), key=lambda c: (c.get("predicted_tier", 3), -c.get("eligibility_score", 0)))

    print(f"Chosen Tier‑1 threshold: {chosen_threshold} {'(dynamic)' if args.dynamic_threshold else '(fixed)'}\n")
    print("📊 Funnel:")
    print(f"   Found raw:            {funnel['found_raw']}")
    print(f"   De‑duplicated:        {funnel['deduped']}")
    print(f"   Likely Tier‑eligible: {funnel['likely_tier_eligible']}")
    print(f"   Predicted Tier‑1:     {funnel['predicted_tier1']}\n")

    print("Top candidates (by predicted tier then score):")
    for i, c in enumerate(rows[:20], start=1):
        types = ",".join(c.get("types", [])[:4])
        web = "yes" if c.get("website") else "no"
        print(f" {i:2d}. [{c.get('predicted_tier')}] {c.get('name')} — score={c.get('eligibility_score'):>3} types={types} website={web} rating={c.get('rating')} ({c.get('user_ratings_total')})")

    # Write CSV
    os.makedirs("output", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join("output", f"test_phase1_{stamp}.csv")
    fieldnames = [
        "place_id", "name", "address", "lat", "lng", "types", "website", "url",
        "rating", "user_ratings_total", "business_status", "eligibility_score", "predicted_tier", "score_reasons",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for c in rows:
            row = {k: c.get(k) for k in fieldnames}
            if isinstance(row.get("types"), list):
                row["types"] = ",".join(row["types"])
            if isinstance(row.get("score_reasons"), list):
                row["score_reasons"] = "; ".join(row["score_reasons"])
            w.writerow(row)

    print(f"\n💾 Saved CSV → {out_path}")
    print("\nTip: Use --center to skip geocode Text Search when testing, and --rate-delay to pace calls if needed.")


if __name__ == "__main__":
    main()
