"""Client for The Movie Database (TMDB) API.

With ``TMDB_API_KEY`` set (a v3 API key or a v4 "API Read Access Token"),
ReelMatch searches TMDB's full catalog of movies instead of the bundled one,
with posters and up-to-date "where to watch" providers (JustWatch data
served through TMDB).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w342"

# TMDB genre ids, keyed by the genre names ReelMatch uses.
GENRE_IDS = {
    "Action": 28, "Adventure": 12, "Animation": 16, "Comedy": 35, "Crime": 80,
    "Documentary": 99, "Drama": 18, "Family": 10751, "Fantasy": 14,
    "History": 36, "Horror": 27, "Music": 10402, "Musical": 10402,
    "Mystery": 9648, "Romance": 10749, "Sci-Fi": 878, "Thriller": 53,
    "War": 10752, "Western": 37,
}
GENRE_RENAME = {"Science Fiction": "Sci-Fi"}

Fetch = Callable[[str, dict], dict]


class TMDBError(RuntimeError):
    pass


def justwatch_link(title: str) -> str:
    return f"https://www.justwatch.com/us/search?q={urllib.parse.quote(title)}"


class TMDB:
    def __init__(self, api_key: str | None = None, region: str | None = None,
                 fetch: Fetch | None = None, ttl: float = 6 * 3600):
        self.api_key = api_key or os.environ.get("TMDB_API_KEY") or os.environ.get("TMDB_READ_TOKEN")
        self.region = (region or os.environ.get("WATCH_REGION") or "US").upper()
        self._fetch = fetch or self._http
        self._ttl = ttl
        self._cache: dict[tuple, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    # ------------------------------------------------------------- transport
    def _http(self, path: str, params: dict) -> dict:
        params = dict(params)
        headers = {"Accept": "application/json"}
        if self.api_key.startswith("eyJ"):          # v4 read access token (JWT)
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:                                        # v3 api key
            params["api_key"] = self.api_key
        url = f"{API}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise TMDBError("TMDB rejected the API key (401)") from e
            raise TMDBError(f"TMDB request failed: HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise TMDBError(f"Could not reach TMDB: {e}") from e

    def get(self, path: str, **params) -> dict:
        key = (path, tuple(sorted(params.items())))
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self._ttl:
                return hit[1]
        data = self._fetch(path, params)
        with self._lock:
            self._cache[key] = (now, data)
        return data

    # ------------------------------------------------------------ endpoints
    def search_movies(self, query: str, year: int | None = None) -> list[dict]:
        params = {"query": query, "include_adult": "false"}
        if year:
            params["year"] = year
        return self.get("/search/movie", **params).get("results", [])

    def search_person(self, query: str) -> list[dict]:
        return self.get("/search/person", query=query, include_adult="false").get("results", [])

    def search_keyword(self, query: str) -> list[dict]:
        return self.get("/search/keyword", query=query).get("results", [])

    def discover(self, **params) -> list[dict]:
        params.setdefault("include_adult", "false")
        params.setdefault("sort_by", "popularity.desc")
        return self.get("/discover/movie", **params).get("results", [])

    def recommendations(self, tmdb_id: int) -> list[dict]:
        return self.get(f"/movie/{tmdb_id}/recommendations").get("results", [])

    def trending(self) -> list[dict]:
        return self.get("/trending/movie/week").get("results", [])

    def find_by_title(self, title: str, year: int | None = None) -> int | None:
        results = self.search_movies(title, year) or (self.search_movies(title) if year else [])
        return results[0]["id"] if results else None

    def details(self, tmdb_id: int) -> dict:
        """Full movie record converted to ReelMatch's catalog format."""
        d = self.get(f"/movie/{tmdb_id}", append_to_response="credits,keywords,watch/providers")
        return self.to_catalog(d)

    # ------------------------------------------------------------ conversion
    def to_catalog(self, d: dict) -> dict:
        credits = d.get("credits") or {}
        directors = [c["name"] for c in credits.get("crew", []) if c.get("job") == "Director"]
        cast = [c["name"] for c in sorted(credits.get("cast", []), key=lambda c: c.get("order", 99))[:5]]
        kws = (d.get("keywords") or {}).get("keywords", [])
        date = d.get("release_date") or ""
        region = ((d.get("watch/providers") or {}).get("results") or {}).get(self.region) or {}
        title = d.get("title") or d.get("original_title") or "Untitled"

        def names(kind):
            return [p["provider_name"] for p in sorted(region.get(kind, []),
                                                      key=lambda p: p.get("display_priority", 99))]

        stream = names("flatrate")
        return {
            "id": f"tmdb-{d['id']}",
            "title": title,
            "year": int(date[:4]) if date[:4].isdigit() else 0,
            "genres": [GENRE_RENAME.get(g["name"], g["name"]) for g in d.get("genres", [])],
            "director": ", ".join(dict.fromkeys(directors)),
            "cast": cast,
            "runtime": d.get("runtime") or 0,
            "rating": round(float(d.get("vote_average") or 0), 1),
            "vote_count": int(d.get("vote_count") or 0),
            "synopsis": d.get("overview") or "No synopsis available.",
            "keywords": [k["name"] for k in kws[:15]],
            "watch": stream,
            "poster": IMG + d["poster_path"] if d.get("poster_path") else None,
            "where_to_watch": {
                "source": "live",
                "stream": stream,
                "free": names("free") + names("ads"),
                "rent": names("rent"),
                "buy": names("buy"),
                "link": region.get("link") or justwatch_link(title),
            },
        }
