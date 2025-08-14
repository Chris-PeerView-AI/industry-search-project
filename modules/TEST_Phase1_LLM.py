from __future__ import annotations
import argparse, json, sys
from typing import Optional, Tuple
from phase1_lib import (
    IndustrySettings, DiscoveryParams, KNOWN_TYPES,
    default_settings_for_industry, build_profile_prompt, ollama_generate_profile_json,
    validate_profile_json, merge_profile, plan_queries, explain_scoring_rules,
)

# NOTE: This runner supports --plan-only (no Google calls) and brand focus controls.

def parse_args():
    p = argparse.ArgumentParser(description="Phase‑1 discovery harness (PLAN MODE; Google calls disabled unless you add them back)")
    p.add_argument("--industry", required=True)
    p.add_argument("--location", required=True)
    p.add_argument("--target", type=int, default=20)
    p.add_argument("--max-radius-km", type=float, default=25.0, dest="max_radius_km")
    p.add_argument("--breadth", choices=["narrow","normal","wide"], default="normal")
    p.add_argument("--grid-step-km", type=float, default=2.5)
    p.add_argument("--center", help='Optional center as "lat,lng"; if omitted, we will show that geocoding is required.')
    p.add_argument("--plan-only", action="store_true", help="Print query plan + scoring rubric only (no API calls)")

    # Brand focus
    p.add_argument("--focus-detail", help="Optional brand/chain or exact business pattern to prioritize (e.g., 'Drybar' or 'Scooter\'s Coffee')")
    p.add_argument("--focus-strict", action="store_true", help="If set, only search by the brand keyword (no generic terms)")

    # LLM profile (Ollama)
    p.add_argument("--enable-llm-profile", action="store_true")
    p.add_argument("--ollama-model", default="llama3")
    p.add_argument("--ollama-url", default="http://localhost:11434")
    p.add_argument("--llm-temp", type=float, default=0.2)

    # Manual overrides (future‑proof; kept short here)
    p.add_argument("--allow-types", help="CSV allow types")
    p.add_argument("--soft-deny-types", help="CSV soft‑deny types")
    p.add_argument("--include-keywords", help="CSV positive keywords")
    p.add_argument("--name-positive", help="CSV name tokens +")
    p.add_argument("--name-negative", help="CSV name tokens −")
    p.add_argument("--early-open-hour", type=int)
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def csv_set(s: Optional[str]):
    if s is None: return None
    if s.strip() == "": return set()
    return {x.strip().lower() for x in s.split(',') if x.strip()}


def apply_overrides(settings: IndustrySettings, args) -> IndustrySettings:
    def pick(default, cli):
        v = csv_set(cli)
        return v if v is not None else default
    settings.allow_types = pick(settings.allow_types, args.allow_types)
    settings.soft_deny_types = pick(settings.soft_deny_types, args.soft_deny_types)
    settings.include_keywords = pick(settings.include_keywords, args.include_keywords)
    settings.name_positive = pick(settings.name_positive, args.name_positive)
    settings.name_negative = pick(settings.name_negative, args.name_negative)
    if args.early_open_hour is not None:
        settings.early_open_hour = args.early_open_hour
    return settings


def parse_center(center_str: Optional[str]) -> Optional[Tuple[float,float]]:
    if not center_str: return None
    try:
        a,b = [x.strip() for x in center_str.split(',')]
        return float(a), float(b)
    except Exception:
        return None


def main():
    args = parse_args()

    # 1) Base settings (generic per industry)
    settings = default_settings_for_industry(args.industry)

    # 2) Optional LLM profile (merges into defaults)
    if args.enable_llm_profile:
        prompt = build_profile_prompt(args.industry, args.location, KNOWN_TYPES)
        prof_raw = ollama_generate_profile_json(args.ollama_model, args.ollama_url, prompt, temperature=args.llm_temp)
        prof = validate_profile_json(prof_raw or {}, KNOWN_TYPES)
        if prof:
            settings = merge_profile(settings, prof)
            settings.profile_source = "llm"
        elif args.verbose:
            print("(LLM profile unavailable; using defaults)")

    # 3) CLI overrides last
    settings = apply_overrides(settings, args)

    # 4) Params & center
    params = DiscoveryParams(breadth=args.breadth, target_count=args.target, max_radius_km=args.max_radius_km, grid_step_km=args.grid_step_km)
    center = parse_center(args.center)

    print(f"\n🚧 PLAN‑ONLY MODE — {args.industry} @ {args.location}")
    print(f"   breadth={args.breadth} (oversample≈{params.oversample_factor}×), max_radius_km={params.max_radius_km}, grid_step_km={params.grid_step_km}")
    print(f"   profile_source={getattr(settings, 'profile_source', 'defaults')}")
    if args.focus_detail:
        print(f"   focus_detail='{args.focus_detail}' strict={args.focus_strict}")

    # 5) Query plan (what we WOULD send to Google)
    plan = plan_queries(center, params, settings, focus_detail=args.focus_detail, focus_strict=args.focus_strict)
    print("\n🔎 Query parameters we would send:")
    print(json.dumps({k:v for k,v in plan.items() if k in ("type_hint","keyword","per_node_radius_m","grid_step_km","max_radius_km","oversample_factor")}, indent=2))

    if not center:
        print("\n(center missing) → We would first geocode the location via Text Search to obtain lat/lng.")
    else:
        print(f"\n📍 Center: {center}")
        print(f"Grid nodes planned: {plan.get('grid_nodes')} (showing first {len(plan.get('grid_preview',[]))})")
        for i, q in enumerate(plan.get('grid_preview', []), start=1):
            print(f" {i:02d}. location={q['location']} radius={q['radius']} type={q.get('type')} keyword={q.get('keyword')}")
            print(f"     e.g., {q['sample_url']}")

    # 6) Scoring rubric (how we will classify results)
    print("\n🧮 Scoring rubric (how each candidate would be evaluated):")
    for line in explain_scoring_rules(settings, args.focus_detail, args.focus_strict):
        print(" - " + line)

    print("\n(When you are ready to run live discovery, remove --plan-only and re‑enable the Google Places calls in your runner.)\n")

if __name__ == "__main__":
    main()