"""Recommendation engines behind the API.

* ``LocalEngine`` ranks the bundled catalog in ``data/movies.json``.
* ``LiveEngine`` searches TMDB's full movie database: it turns the query into
  TMDB lookups (people, keywords, genres, years, "movies like X"), pulls the
  candidate movies' details, and ranks them with the same content-based
  recommender used for the local catalog.

Both return movies in the same shape, so the API and frontend don't care
which one is active.
"""

from __future__ import annotations

import re
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from recommender import STOPWORDS, SYNONYMS, Recommender, _words
from tmdb import GENRE_IDS, TMDB, TMDBError, justwatch_link

LIKE_RE = re.compile(r"\b(?:like|similar to|such as|same as)\s+(.+)$", re.I)


# --------------------------------------------------------------------- local
class LocalEngine:
    name = "catalog"

    def __init__(self, recommender: Recommender):
        self.r = recommender

    @staticmethod
    def _decorate(m: dict) -> dict:
        m = dict(m)
        q = urllib.parse.quote(f"{m['title']} {m['year']}")
        m["where_to_watch"] = {
            "source": "catalog",
            "stream": m["watch"],
            "free": [],
            "rent": ["Apple TV", "Prime Video", "Google Play"],
            "buy": [],
            "link": justwatch_link(m["title"]),
            "search": f"https://www.google.com/search?q=watch+{q}",
        }
        return m

    def valid_id(self, movie_id: str) -> bool:
        return movie_id in self.r.by_id

    def resolve(self, movie_id: str) -> str | None:
        return movie_id if movie_id in self.r.by_id else None

    def search(self, query: str, limit: int, exclude: set[str]) -> dict:
        res = self.r.search(query, limit=limit, exclude=exclude)
        res["results"] = [self._decorate(m) for m in res["results"]]
        return res

    def recommend(self, history: list[dict], limit: int) -> list[dict]:
        return [self._decorate(m) for m in self.r.from_history(history, limit=limit)]

    def get(self, movie_id: str) -> dict | None:
        m = self.r.by_id.get(movie_id)
        if not m:
            return None
        movie = self._decorate(m.to_dict())
        movie["similar"] = [self._decorate(s) for s in self.r.similar(movie_id, limit=6)]
        return movie

    def get_many(self, ids: list[str]) -> dict[str, dict]:
        return {i: self._decorate(self.r.by_id[i].to_dict()) for i in ids if i in self.r.by_id}

    def lookup(self, query: str) -> list[dict]:
        return self.r.lookup(query)

    def trending(self, limit: int) -> list[dict]:
        top = sorted(self.r.movies, key=lambda m: (-m.rating, -m.year))[:limit]
        return [self._decorate(m.to_dict()) for m in top]


# ---------------------------------------------------------------------- live
class LiveEngine:
    name = "tmdb"
    MAX_DETAILS = 40

    def __init__(self, tmdb: TMDB, local: Recommender):
        self.tmdb = tmdb
        self.local = local           # used for query parsing (genres, decades, names)
        self.pool = ThreadPoolExecutor(max_workers=10)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def tmdb_id(movie_id: str) -> int | None:
        if movie_id.startswith("tmdb-") and movie_id[5:].isdigit():
            return int(movie_id[5:])
        return None

    def valid_id(self, movie_id: str) -> bool:
        return self.tmdb_id(movie_id) is not None or movie_id in self.local.by_id

    def resolve(self, movie_id: str) -> str | None:
        """Map a bundled-catalog id (e.g. from older history) to a TMDB id."""
        if self.tmdb_id(movie_id) is not None:
            return movie_id
        m = self.local.by_id.get(movie_id)
        if not m:
            return None
        found = self.tmdb.find_by_title(m.title, m.year)
        return f"tmdb-{found}" if found else None

    def _details(self, ids) -> list[dict]:
        def one(i):
            try:
                return self.tmdb.details(i)
            except TMDBError:
                return None
        return [d for d in self.pool.map(one, ids) if d]

    def _parallel(self, fn, items):
        def safe(x):
            try:
                return fn(x)
            except TMDBError:
                return []
        return list(self.pool.map(safe, items))

    # ------------------------------------------------------ query → lookups
    def _find_people(self, words: list[str], known: list[str]) -> list[dict]:
        """Person ids for names in the query (exact full-name matches only)."""
        phrases = []
        for n in (3, 2):
            for i in range(len(words) - n + 1):
                chunk = words[i:i + n]
                if not any(w in STOPWORDS for w in chunk):
                    phrases.append(" ".join(chunk))
        phrases = list(dict.fromkeys([k.lower() for k in known] + phrases))[:10]
        found = {}
        for phrase, results in zip(phrases, self._parallel(self.tmdb.search_person, phrases)):
            for p in results[:3]:
                if " ".join(_words(p.get("name", ""))) == " ".join(_words(phrase)):
                    found[p["id"]] = p
                    break
        return list(found.values())

    def _find_keywords(self, words: list[str]) -> list[dict]:
        terms = []
        for n in (2, 1):
            for i in range(len(words) - n + 1):
                chunk = words[i:i + n]
                if all(w not in STOPWORDS and len(w) > 2 for w in chunk):
                    terms.append(" ".join(chunk))
        for w in words:
            for syn in SYNONYMS.get(w, []):
                if syn.replace("_", " ").title() not in GENRE_IDS:
                    terms.append(syn.replace("_", " "))
        terms = list(dict.fromkeys(terms))[:10]
        found = {}
        for term, results in zip(terms, self._parallel(self.tmdb.search_keyword, terms)):
            variants = {term, term + "s", term.rstrip("s")}
            for k in results[:5]:
                if " ".join(_words(k.get("name", ""))) in variants:
                    found[k["id"]] = k
                    break
        return list(found.values())

    def _date_params(self, years) -> dict:
        if not years:
            return {}
        return {"primary_release_date.gte": f"{years[0]}-01-01",
                "primary_release_date.lte": f"{years[1]}-12-31"}

    # ------------------------------------------------------------- search
    def search(self, query: str, limit: int, exclude: set[str]) -> dict:
        parsed = self.local.parse_query(query)
        years = parsed["year_range"]
        genre_ids = [GENRE_IDS[g] for g in dict.fromkeys(parsed["genres"]) if g in GENRE_IDS]
        prior: Counter = Counter()
        pool_ids: list[int] = []

        def add(results, weight):
            for rank, r in enumerate(results):
                prior[r["id"]] += weight * (1 - rank / 40)
                pool_ids.append(r["id"])

        # 1. "movies like X"
        ref = None
        m = LIKE_RE.search(query)
        if m:
            hits = self.tmdb.search_movies(m.group(1).strip(" ?.!"))
            if hits:
                ref = hits[0]
                add(self.tmdb.recommendations(ref["id"])[:30], 0.30)

        rest = LIKE_RE.sub("", query) if ref else query
        words = _words(rest)
        # Drop decade / year tokens so they aren't looked up as names or keywords.
        words = [w for w in words if not re.fullmatch(r"(19|20)?\d0s|\d{4}", w)]

        # 2. People and keywords (looked up in parallel)
        people_f = self.pool.submit(self._find_people, words, parsed["people"])
        genre_words = {w for g in parsed["genres"] for w in _words(g)} | {"sci", "fi", "scifi"}
        kw_words = [w for w in words if w not in genre_words]
        keywords_f = self.pool.submit(self._find_keywords, kw_words)
        try:
            people = people_f.result()
        except TMDBError:
            people = []
        try:
            keywords = keywords_f.result()
        except TMDBError:
            keywords = []
        person_words = {w for p in people for w in _words(p["name"])}
        keywords = [k for k in keywords if not set(_words(k["name"])) <= person_words]

        base = self._date_params(years)
        if genre_ids:
            base["with_genres"] = (",".join if len(genre_ids) <= 2 else "|".join)(map(str, genre_ids))
        calls = []
        if people:
            ids = "|".join(str(p["id"]) for p in people)
            calls.append(({**base, "with_people": ids, "sort_by": "vote_count.desc"}, 0.45))
            calls.append(({**self._date_params(years), "with_people": ids,
                           "sort_by": "popularity.desc"}, 0.35))
        if keywords:
            kw = "|".join(str(k["id"]) for k in keywords)
            calls.append(({**base, "with_keywords": kw, "sort_by": "vote_count.desc",
                           "vote_count.gte": 50}, 0.30))
            calls.append(({**self._date_params(years), "with_keywords": kw,
                           "sort_by": "popularity.desc", "vote_count.gte": 20}, 0.15))
        if (genre_ids or years) and not people:
            calls.append(({**base, "sort_by": "vote_count.desc"}, 0.20))
            calls.append(({**base, "sort_by": "vote_average.desc", "vote_count.gte": 1000}, 0.15))
        for (params, weight), results in zip(
                calls, self._parallel(lambda p: self.tmdb.discover(**p), [c[0] for c in calls])):
            add(results, weight)

        # 3. Plain title search catches queries that name a movie.
        if not ref:
            try:
                add(self.tmdb.search_movies(query)[:5], 0.10)
            except TMDBError:
                pass

        # 4. Thin pool: widen it with TMDB's recommendations for the best hits so
        #    there are always enough related movies to fill a page.
        unique = list(dict.fromkeys(sorted(pool_ids, key=lambda i: -prior[i])))
        if 0 < len(unique) < 2 * limit:
            for recs in self._parallel(self.tmdb.recommendations, unique[:3]):
                add(recs[:15], 0.05)

        if not pool_ids:
            add(self.tmdb.trending(), 0.05)

        exclude_ids = {self.tmdb_id(e) for e in exclude} - {None}
        ordered = [i for i in dict.fromkeys(sorted(pool_ids, key=lambda i: -prior[i]))
                   if i not in exclude_ids][: self.MAX_DETAILS]
        if ref:
            ordered.append(ref["id"])
        movies = self._details(ordered)
        if not movies:
            return {"query": query, "results": [],
                    "interpreted": {"people": [], "genres": [], "similar_to": [], "years": None}}

        engine = Recommender(movies)
        ranked = engine.search(
            query, limit=limit,
            exclude=set(exclude) | ({f"tmdb-{ref['id']}"} if ref else set()),
            prior={f"tmdb-{i}": min(p, 0.6) for i, p in prior.items()},
        )
        interp = ranked["interpreted"]
        interp["people"] = list(dict.fromkeys(interp["people"] + [p["name"] for p in people]))
        interp["genres"] = list(dict.fromkeys(parsed["genres"] + interp["genres"]))
        interp["similar_to"] = [ref["title"]] if ref else interp["similar_to"]
        interp["years"] = list(years) if years else None
        interp["keywords"] = [k["name"] for k in keywords]
        return ranked

    # ---------------------------------------------------------- history
    def recommend(self, history: list[dict], limit: int) -> list[dict]:
        resolved = []
        for h in history[-15:]:
            rid = self.resolve(h["movie_id"])
            if rid:
                resolved.append({**h, "movie_id": rid})
        if not resolved:
            return []
        watched_ids = [self.tmdb_id(h["movie_id"]) for h in resolved]
        seen = set(watched_ids) | {self.tmdb_id(h["movie_id"]) for h in history}
        prior: Counter = Counter()
        n = len(resolved)
        rec_lists = self._parallel(self.tmdb.recommendations, watched_ids)
        for i, (h, recs) in enumerate(zip(resolved, rec_lists)):
            if h.get("liked") is False:
                continue
            weight = (1.3 if h.get("liked") else 1.0) * (0.6 + 0.4 * (i + 1) / n)
            for rank, r in enumerate(recs[:20]):
                prior[r["id"]] += 0.08 * weight * (1 - rank / 25)
        candidates = [i for i, _ in prior.most_common() if i not in seen][: self.MAX_DETAILS]
        movies = self._details(list(dict.fromkeys(watched_ids + candidates)))
        engine = Recommender(movies)
        hist = [h for h in resolved if h["movie_id"] in engine.by_id]
        return engine.from_history(hist, limit=limit,
                                   prior={f"tmdb-{i}": min(p, 0.4) for i, p in prior.items()})

    # ------------------------------------------------------------ details
    def get(self, movie_id: str) -> dict | None:
        rid = self.resolve(movie_id)
        if not rid:
            return None
        tid = self.tmdb_id(rid)
        movie = self.tmdb.details(tid)
        try:
            recs = [r["id"] for r in self.tmdb.recommendations(tid)[:15]]
        except TMDBError:
            recs = []
        others = self._details(recs)
        movie["similar"] = Recommender([movie] + others).similar(rid, limit=6) if others else []
        return movie

    def get_many(self, ids: list[str]) -> dict[str, dict]:
        pairs = [(i, self.tmdb_id(r)) for i, r in zip(ids, self._parallel(self.resolve, ids)) if r]
        by_tid = {m["id"]: m for m in self._details([t for _, t in pairs])}
        return {i: by_tid[f"tmdb-{t}"] for i, t in pairs if f"tmdb-{t}" in by_tid}

    def lookup(self, query: str) -> list[dict]:
        out = []
        for r in self.tmdb.search_movies(query)[:8]:
            date = r.get("release_date") or ""
            out.append({"id": f"tmdb-{r['id']}", "title": r.get("title", ""),
                        "year": int(date[:4]) if date[:4].isdigit() else None})
        return out

    def trending(self, limit: int) -> list[dict]:
        return self._details([r["id"] for r in self.tmdb.trending()[:limit]])
