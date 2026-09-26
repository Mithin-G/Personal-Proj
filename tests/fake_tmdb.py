"""An offline stand-in for the TMDB API, backed by the bundled catalog.

It answers the same endpoints ReelMatch calls with TMDB-shaped JSON, so the
live engine can be tested end to end without network access or an API key.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from recommender import Recommender, _words  # noqa: E402
from tmdb import GENRE_IDS  # noqa: E402

GENRE_NAMES = {v: k for k, v in GENRE_IDS.items() if k != "Musical"}
GENRE_NAMES[878] = "Science Fiction"


def _norm(s):
    return " ".join(_words(s))


class FakeTMDB:
    def __init__(self):
        self.r = Recommender.from_file()
        self.calls = []
        self.fail = False
        self.movies = {}
        self.people = {}
        self.keywords = {}
        for i, m in enumerate(self.r.movies):
            tid = 1000 + i
            self.movies[tid] = m
            for p in m.cast + m.directors:
                self.people.setdefault(p, 5000 + len(self.people))
            for k in m.keywords:
                self.keywords.setdefault(k, 9000 + len(self.keywords))
        self.tid = {m.id: t for t, m in self.movies.items()}

    def _genre_ids(self, m):
        return [GENRE_IDS[g] for g in m.genres if g in GENRE_IDS]

    def _summary(self, tid):
        m = self.movies[tid]
        return {"id": tid, "title": m.title, "release_date": f"{m.year}-06-01",
                "vote_average": m.rating, "vote_count": 5000, "genre_ids": self._genre_ids(m)}

    def __call__(self, path, params):
        from tmdb import TMDBError
        self.calls.append((path, params))
        if self.fail:
            raise TMDBError("simulated outage")
        if path == "/search/movie":
            q = _norm(params["query"])
            hits = [t for t, m in self.movies.items()
                    if q and (q in _norm(m.title) or _norm(m.title) in q)]
            if params.get("year"):
                hits = [t for t in hits if self.movies[t].year == int(params["year"])] or []
            hits.sort(key=lambda t: (_norm(self.movies[t].title) != q, -self.movies[t].rating))
            return {"results": [self._summary(t) for t in hits]}
        if path == "/search/person":
            q = _norm(params["query"])
            return {"results": [{"id": pid, "name": n} for n, pid in self.people.items()
                                if q and q in _norm(n)]}
        if path == "/search/keyword":
            q = _norm(params["query"])
            return {"results": [{"id": kid, "name": k} for k, kid in self.keywords.items()
                                if q and q in _norm(k)]}
        if path == "/discover/movie":
            return {"results": [self._summary(t) for t in self._discover(params)][:20]}
        if path == "/trending/movie/week":
            top = sorted(self.movies, key=lambda t: -self.movies[t].year)[:20]
            return {"results": [self._summary(t) for t in top]}
        m = re.fullmatch(r"/movie/(\d+)/recommendations", path)
        if m:
            base = self.movies[int(m.group(1))]
            sims = self.r.similar(base.id, limit=20)
            return {"results": [self._summary(self.tid[s["id"]]) for s in sims]}
        m = re.fullmatch(r"/movie/(\d+)", path)
        if m:
            return self._details(int(m.group(1)))
        raise TMDBError(f"unexpected path {path}")

    def _discover(self, p):
        out = []
        for t, m in self.movies.items():
            if "with_people" in p:
                ids = {int(x) for x in str(p["with_people"]).split("|")}
                if not ids & {self.people[x] for x in m.cast + m.directors}:
                    continue
            if "with_keywords" in p:
                ids = {int(x) for x in str(p["with_keywords"]).split("|")}
                if not ids & {self.keywords[k] for k in m.keywords}:
                    continue
            if "with_genres" in p:
                g = str(p["with_genres"])
                want = {int(x) for x in re.split(r"[,|]", g)}
                have = set(self._genre_ids(m))
                if ("|" in g and not want & have) or ("|" not in g and not want <= have):
                    continue
            if "primary_release_date.gte" in p and m.year < int(p["primary_release_date.gte"][:4]):
                continue
            if "primary_release_date.lte" in p and m.year > int(p["primary_release_date.lte"][:4]):
                continue
            out.append(t)
        key = {"vote_average.desc": lambda t: -self.movies[t].rating}.get(
            p.get("sort_by"), lambda t: (-self.movies[t].rating, t))
        return sorted(out, key=key)

    def _details(self, tid):
        m = self.movies[tid]
        return {
            "id": tid, "title": m.title, "release_date": f"{m.year}-06-01",
            "genres": [{"id": g, "name": GENRE_NAMES[g]} for g in self._genre_ids(m)],
            "runtime": m.runtime, "vote_average": m.rating, "vote_count": 5000,
            "overview": m.synopsis, "poster_path": f"/{tid}.jpg",
            "credits": {
                "cast": [{"name": c, "order": i} for i, c in enumerate(m.cast)],
                "crew": [{"name": d, "job": "Director"} for d in m.directors],
            },
            "keywords": {"keywords": [{"id": self.keywords[k], "name": k} for k in m.keywords]},
            "watch/providers": {"results": {"US": {
                "link": f"https://www.themoviedb.org/movie/{tid}/watch",
                "flatrate": [{"provider_name": w, "display_priority": i} for i, w in enumerate(m.watch)],
                "rent": [{"provider_name": "Apple TV", "display_priority": 1}],
            }}},
        }
