from __future__ import annotations
import json, math, re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Set, Iterable

# -----------------------------
# Data classes & constants
# -----------------------------

@dataclass
class IndustrySettings:
    allow_types: Set[str] = field(default_factory=set)
    soft_deny_types: Set[str] = field(default_factory=set)
    include_keywords: Set[str] = field(default_factory=set)
    exclude_keywords: Set[str] = field(default_factory=set)
    name_positive: Set[str] = field(default_factory=set)
    name_negative: Set[str] = field(default_factory=set)
    early_open_hour: Optional[int] = None
    bakery_demote: bool = False
    weights: Dict[str, int] = field(default_factory=lambda: {
        "allow_types": 35, "soft_deny": -25,
        "name_pos_base": 10, "name_pos_step": 5, "name_neg_base": -10,
        "early_open_bonus": 10, "rating_bonus": 5, "website_bonus": 5,
        # generic extras
        "low_quality_penalty": -15, "restaurant_cafe_cap": -10,
        "focus_brand_bonus": 15,
    })
    threshold_candidates: List[int] = field(default_factory=lambda: [80, 75, 70, 65, 60, 55])
    floor_ratio: float = 0.6
    category_demotions: List[Dict[str, object]] = field(default_factory=list)
    profile_source: str = "defaults"

@dataclass
class DiscoveryParams:
    breadth: str
    target_count: int
    max_radius_km: float
    grid_step_km: float = 2.5
    per_node_radius_m: Tuple[int, ...] = (1000, 2500, 5000)
    oversample_factor: int = 3
    def __post_init__(self):
        bm = {"narrow": 2, "normal": 3, "wide": 4}
        if self.breadth not in bm:
            raise ValueError("breadth must be one of: narrow|normal|wide")
        self.oversample_factor = bm[self.breadth]

# Google types we recognize (cross‑industry)
KNOWN_TYPES: Set[str] = {
    "cafe","coffee_shop","restaurant","bar","night_club","market","grocery_or_supermarket",
    "point_of_interest","establishment","food","store","bakery","shopping_mall","supermarket",
    "hair_salon","beauty_salon","spa","barber_shop","bowling_alley","car_wash","gym","book_store",
}

# -----------------------------
# Geometry helpers (for planning only)
# -----------------------------
EARTH_RADIUS_KM = 6371.0088

def km_to_deg_lat(km: float) -> float: return km / 111.0

def km_to_deg_lon(km: float, lat_deg: float) -> float:
    return km / (111.320 * math.cos(math.radians(lat_deg)) or 1e-9)


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

# -----------------------------
# LLM profile (optional, via Ollama HTTP)
# -----------------------------

def build_profile_prompt(industry: str, location: str, known_types: Set[str]) -> str:
    allowed_types = ", ".join(sorted(known_types))
    schema = (
        "{\n"
        "  \"allow_types\": [\"cafe\"],\n"
        "  \"soft_deny_types\": [\"restaurant\"],\n"
        "  \"name_positive\": [\"coffee\"],\n"
        "  \"name_negative\": [\"market\"],\n"
        "  \"include_keywords\": [\"espresso\"],\n"
        "  \"exclude_keywords\": [\"banquet\"],\n"
        "  \"early_open_hour\": 7,\n"
        "  \"category_demotions\": [{\"type\": \"bakery\", \"delta\": -20}],\n"
        "  \"weights\": {\n"
        "    \"allow_types\": 35, \"soft_deny\": -25, \"name_pos_base\": 10, \"name_pos_step\": 5, \"name_neg_base\": -10, \"early_open_bonus\": 10, \"rating_bonus\": 5, \"website_bonus\": 5, \"focus_brand_bonus\": 15\n"
        "  },\n"
        "  \"threshold_candidates\": [80,75,70,65,60,55],\n"
        "  \"floor_ratio\": 0.6\n"
        "}"
    )
    return f"""
You are generating a neutral, cross‑industry Google Places scoring profile.
Industry: {industry}\nLocation: {location}
Choose only from this allowed Google types list:\n{allowed_types}
Return STRICT JSON that matches this schema (values may change, structure must not):\n{schema}
Constraints:\n- allow/deny types must come from the allowed list.\n- Weights must be reasonable and integers.\n- early_open_hour: integer or null.\n- threshold_candidates: descending ints between 50..90; floor_ratio in [0.3..0.9].
"""


def ollama_generate_profile_json(model: str, url_base: str, prompt: str, temperature: float = 0.2, timeout: int = 60) -> Optional[Dict]:
    import requests
    try:
        resp = requests.post(f"{url_base.rstrip('/')}/api/generate", json={
            "model": model, "prompt": prompt, "stream": False,
            "temperature": max(0.0, min(1.0, float(temperature)))
        }, timeout=timeout)
        resp.raise_for_status(); data = resp.json(); text = data.get("response") or ""
        m = re.search(r"\{[\s\S]*\}", text)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def validate_profile_json(profile: Dict, allowed_types: Set[str]) -> Dict:
    if not isinstance(profile, dict):
        return {}
    out: Dict[str, object] = {}
    def _as_list_str(key: str) -> List[str]:
        vals = profile.get(key) or []
        if not isinstance(vals, list): return []
        res = []
        for v in vals:
            s = str(v).strip().lower()
            if key.endswith("types"):
                if s in allowed_types: res.append(s)
            elif s: res.append(s)
        # de‑dupe keep order, cap 20
        seen, dedup = set(), []
        for x in res:
            if x not in seen:
                seen.add(x); dedup.append(x)
        return dedup[:20]

    out["allow_types"] = set(_as_list_str("allow_types"))
    out["soft_deny_types"] = set(_as_list_str("soft_deny_types"))
    out["name_positive"] = set(_as_list_str("name_positive"))
    out["name_negative"] = set(_as_list_str("name_negative"))
    out["include_keywords"] = set(_as_list_str("include_keywords"))
    out["exclude_keywords"] = set(_as_list_str("exclude_keywords"))

    try:
        eh = profile.get("early_open_hour")
        out["early_open_hour"] = int(eh) if eh is not None else None
    except Exception:
        out["early_open_hour"] = None

    w = {k:int(profile.get("weights",{}).get(k)) for k in (
        "allow_types","soft_deny","name_pos_base","name_pos_step","name_neg_base",
        "early_open_bonus","rating_bonus","website_bonus","focus_brand_bonus"
    ) if str(profile.get("weights",{}).get(k)).lstrip("-").isdigit()}
    out["weights"] = w

    ths = [int(t) for t in (profile.get("threshold_candidates") or []) if str(t).isdigit()]
    out["threshold_candidates"] = sorted(set([t for t in ths if 50 <= t <= 90]), reverse=True) or [80,75,70,65,60,55]
    fr = profile.get("floor_ratio")
    out["floor_ratio"] = float(fr) if isinstance(fr,(int,float)) else 0.6
    return out

# Merge LLM profile into defaults

def merge_profile(base: IndustrySettings, prof: Dict) -> IndustrySettings:
    def _merge_set(a: Set[str], b: Iterable[str]|Set[str]|None) -> Set[str]:
        out = set(a)
        if b: out |= {str(x).lower() for x in b}
        return out
    base.allow_types = _merge_set(base.allow_types, prof.get("allow_types"))
    base.soft_deny_types = _merge_set(base.soft_deny_types, prof.get("soft_deny_types"))
    base.name_positive = _merge_set(base.name_positive, prof.get("name_positive"))
    base.name_negative = _merge_set(base.name_negative, prof.get("name_negative"))
    base.include_keywords = _merge_set(base.include_keywords, prof.get("include_keywords"))
    base.exclude_keywords = _merge_set(base.exclude_keywords, prof.get("exclude_keywords"))
    if prof.get("early_open_hour") is not None and base.early_open_hour is None:
        base.early_open_hour = int(prof["early_open_hour"]) if isinstance(prof["early_open_hour"], int) else None
    # weights + thresholds
    base.weights.update(prof.get("weights", {}))
    ths = prof.get("threshold_candidates") or []
    base.threshold_candidates = sorted(set(base.threshold_candidates) | set(ths), reverse=True)
    if isinstance(prof.get("floor_ratio"),(int,float)):
        base.floor_ratio = float(prof["floor_ratio"])
    base.profile_source = "llm"
    return base

# -----------------------------
# Industry defaults (generic)
# -----------------------------

def default_settings_for_industry(industry: str) -> IndustrySettings:
    s = industry.strip().lower()
    st = IndustrySettings()
    # a couple of light hints by category name; otherwise neutral
    if any(tok in s for tok in ["coffee","cafe"]):
        st.allow_types = {"cafe","coffee_shop"}
        st.soft_deny_types = {"restaurant","bar","night_club","market","grocery_or_supermarket"}
        st.name_positive = {"coffee","espresso","brew","roast","cafe"}
        st.name_negative = {"market","grill","palace","lounge","club","eatery"}
        st.include_keywords = {"coffee","espresso","latte"}
        st.early_open_hour = 7
        st.bakery_demote = True
    elif any(tok in s for tok in ["hair","salon","barber"]):
        st.allow_types = {"hair_salon","beauty_salon","barber_shop"}
        st.soft_deny_types = {"spa"}
        st.name_positive = {"salon","hair","barber","blowout","stylist"}
        st.include_keywords = {"haircut","stylist","color"}
    elif "bowling" in s:
        st.allow_types = {"bowling_alley"}
    elif "car wash" in s or "carwash" in s:
        st.allow_types = {"car_wash"}
    else:
        # neutral fallback: no allow_types → LLM or user overrides should fill in
        st.allow_types = set()
    return st

# -----------------------------
# Planning & scoring explanation
# -----------------------------

def compose_keyword(settings: IndustrySettings, focus_detail: Optional[str], focus_strict: bool) -> Tuple[Optional[str], Optional[str]]:
    """Return (type_hint, keyword) for Nearby.
    focus_strict → keyword is the brand only; otherwise include brand in positives.
    """
    type_hint = next(iter(settings.allow_types)) if settings.allow_types else None
    keyword = None
    if focus_detail:
        if focus_strict:
            keyword = focus_detail
        else:
            kw = set(settings.include_keywords)
            kw.add(focus_detail)
            keyword = " ".join(sorted(kw))[:128]
    else:
        if settings.include_keywords:
            keyword = " ".join(sorted(settings.include_keywords))[:128]
    return type_hint, keyword


def plan_queries(center: Tuple[float,float]|None, params: DiscoveryParams, settings: IndustrySettings,
                 focus_detail: Optional[str]=None, focus_strict: bool=False,
                 sample_nodes: int = 10) -> Dict:
    plan: Dict = {
        "center": center or "(geocode_required)",
        "per_node_radius_m": list(params.per_node_radius_m),
        "grid_step_km": params.grid_step_km,
        "max_radius_km": params.max_radius_km,
        "oversample_factor": params.oversample_factor,
        "queries": []
    }
    type_hint, keyword = compose_keyword(settings, focus_detail, focus_strict)
    plan["type_hint"] = type_hint
    plan["keyword"] = keyword

    if not center:
        # no grid if we don't know the point yet
        plan["grid_preview"] = []
        return plan

    lat0, lon0 = center
    grid = generate_grid(lat0, lon0, params.max_radius_km, params.grid_step_km)
    plan["grid_nodes"] = len(grid)
    preview = []
    for (lat, lon, rkm) in grid[:sample_nodes]:
        for rad in params.per_node_radius_m:
            q = {"location": f"{lat:.6f},{lon:.6f}", "radius": rad, "type": type_hint, "keyword": keyword}
            # sample URL with API key placeholder
            base = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
            parts = [f"location={q['location']}", f"radius={rad}"]
            if type_hint: parts.append(f"type={type_hint}")
            if keyword: parts.append(f"keyword={keyword.replace(' ', '+')}")
            parts.append("key=<API_KEY>")
            q["sample_url"] = base + "?" + "&".join(parts)
            preview.append(q)
            break  # one radius per node in preview to keep it short
        if len(preview) >= sample_nodes:
            break
    plan["grid_preview"] = preview
    return plan


def explain_scoring_rules(settings: IndustrySettings, focus_detail: Optional[str], focus_strict: bool) -> List[str]:
    w = settings.weights
    lines = [
        f"Type allow (+{w.get('allow_types',35)}) when candidate has any of: {sorted(settings.allow_types) or '—'}",
        f"Soft‑deny ({w.get('soft_deny',-25)}) when candidate has any of: {sorted(settings.soft_deny_types) or '—'}",
        f"Name positive (+{w.get('name_pos_base',10)}+{w.get('name_pos_step',5)}×) for tokens: {sorted(settings.name_positive) or '—'}",
        f"Name negative ({w.get('name_neg_base',-10)}) for tokens: {sorted(settings.name_negative) or '—'}",
        f"Early‑open ≤ {settings.early_open_hour}+0.5h (+{w.get('early_open_bonus',10)})" if settings.early_open_hour is not None else "Early‑open: n/a",
        f"Rating ≥3.8 & reviews ≥25 (+{w.get('rating_bonus',5)})",
        f"Website present (+{w.get('website_bonus',5)})",
    ]
    if focus_detail:
        lines.append(f"Focus brand match '{focus_detail}' (+{w.get('focus_brand_bonus',15)}) — {'strict (brand only)' if focus_strict else 'non‑strict (brand + generic keywords)'}")
    lines.append(f"Tier‑1 threshold candidates: {settings.threshold_candidates}; floor_ratio={settings.floor_ratio}")
    return lines

# Utilities used by TEST runner

def assign_predicted_tier(score: int, tier1_threshold: int) -> int:
    if score >= tier1_threshold: return 1
    if score >= 50: return 2
    return 3


def choose_tier1_threshold(scores: Iterable[int], target: int, floor_ratio: float, thresholds: List[int]) -> int:
    scores = list(scores)
    likely_eligible = sum(1 for s in scores if s >= 50)
    required = max(1, min(target, int(round(floor_ratio * max(0, likely_eligible)))))
    best_t, best_count = thresholds[-1], -1
    for t in sorted(set(thresholds), reverse=True):
        cnt = sum(1 for s in scores if s >= t)
        if cnt >= required: return t
        if cnt > best_count or (cnt == best_count and t < best_t): best_t, best_count = t, cnt
    return best_t