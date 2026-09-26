"""Optional live data from The Movie Database (TMDB).

When the ``TMDB_API_KEY`` environment variable is set, the app enriches
movies with poster images and up-to-date "where to watch" providers
(powered by JustWatch data via TMDB). Without a key everything still works
using the curated catalog data.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.parse
import urllib.request

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w342"
LOGO = "https://image.tmdb.org/t/p/w92"


class TMDB:
    def __init__(self, api_key: str | None = None, region: str | None = None):
        self.api_key = api_key or os.environ.get("TMDB_API_KEY")
        self.region = (region or os.environ.get("WATCH_REGION") or "US").upper()
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, **params) -> dict:
        params["api_key"] = self.api_key
        url = f"{API}{path}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=6) as resp:
            return json.load(resp)

    def details(self, title: str, year: int) -> dict:
        """Return {"poster": url|None, "providers": {...}|None} for a movie, cached."""
        key = f"{title}|{year}"
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        result = {"poster": None, "providers": None, "tmdb_id": None}
        if self.enabled:
            try:
                found = self._get("/search/movie", query=title, year=year).get("results") or []
                if not found:
                    found = self._get("/search/movie", query=title).get("results") or []
                if found:
                    hit = found[0]
                    result["tmdb_id"] = hit["id"]
                    if hit.get("poster_path"):
                        result["poster"] = IMG + hit["poster_path"]
                    prov = self._get(f"/movie/{hit['id']}/watch/providers")
                    region = (prov.get("results") or {}).get(self.region)
                    if region:
                        result["providers"] = {
                            "link": region.get("link"),
                            **{
                                kind: [
                                    {"name": p["provider_name"],
                                     "logo": LOGO + p["logo_path"] if p.get("logo_path") else None}
                                    for p in region.get(kind, [])
                                ]
                                for kind in ("flatrate", "free", "ads", "rent", "buy")
                            },
                        }
            except Exception:  # network/API failure: fall back to curated data, retry later
                return result
        with self._lock:
            self._cache[key] = result
        return result
