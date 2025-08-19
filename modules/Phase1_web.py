# ================================
# FILE: modules/Phase1_web.py
# PURPOSE: Lightweight website scrape + schema.org types
# ================================

from __future__ import annotations
import re, json, requests
from bs4 import BeautifulSoup
from typing import Dict, Any, List

def _extract_schema_types_ldjson(soup: BeautifulSoup) -> List[str]:
    types: List[str] = []
    for tag in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        def _collect(obj):
            if isinstance(obj, dict):
                t = obj.get("@type")
                if isinstance(t, str): types.append(t)
                elif isinstance(t, list): types.extend([str(x) for x in t])
                for v in obj.values(): _collect(v)
            elif isinstance(obj, list):
                for v in obj: _collect(v)
        _collect(data)
    norm, seen = [], set()
    for t in types:
        tnorm = re.sub(r"[^A-Za-z]", "", t).lower()
        if tnorm and tnorm not in seen:
            norm.append(tnorm); seen.add(tnorm)
    return norm[:12]

def scrape_site(url: str) -> Dict[str, Any]:
    if not url: return {}
    try:
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=8)
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" not in ctype: return {}
        soup = BeautifulSoup(r.text, "html.parser")
        page_title = soup.title.string if soup.title else ""
        meta_desc = (soup.find("meta", attrs={"name":"description"}) or {}).get("content","") or ""
        headers_text = " ".join(h.get_text(strip=True) for h in soup.find_all(re.compile("h[1-3]")))[:2000]
        visible_text = " ".join(p.get_text(strip=True) for p in soup.find_all("p"))[:2000]
        schema_types = _extract_schema_types_ldjson(soup)
        return {
            "page_title": page_title,
            "meta_description": meta_desc,
            "headers": headers_text,
            "visible_text_blocks": visible_text,
            "schema_types": schema_types,
        }
    except Exception:
        return {}
