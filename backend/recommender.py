"""Content-based movie recommender.

Every movie is turned into a weighted TF-IDF vector built from its genres,
keywords, cast, director, title and synopsis. Free-text queries are parsed into
the same vector space (with synonym expansion, people-name matching, title
matching and decade filters) and ranked by cosine similarity. Watch-history
recommendations build a "taste profile" vector from the movies a user has seen.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data" / "movies.json"

STOPWORDS = set(
    """a an the and or but of to in on at for with without from by about as into
    like through over after before between under is are was were be been being it
    its this that these those i me my we our you your he she they them his her
    their what which who whom where when why how all any some no not only own same
    so than too very can will just do does did want wanna looking look find show
    give me movie movies film films something some kind sort type similar good
    great best really recommend recommendation recommendations watch watching
    one ones stuff things thing lot bit maybe please also more most set has have
    had there here get got make made feel feels feeling vibe vibes""".split()
)

# Common everyday words mapped onto vocabulary the catalog actually uses.
SYNONYMS: dict[str, list[str]] = {
    "scary": ["horror", "scary"], "spooky": ["horror", "supernatural"],
    "creepy": ["horror", "psychological"], "frightening": ["horror"],
    "terrifying": ["horror"], "horror": ["horror"],
    "funny": ["comedy", "funny"], "hilarious": ["comedy", "funny"],
    "comedic": ["comedy"], "laugh": ["comedy"], "comedy": ["comedy"],
    "romantic": ["romance", "love"], "romcom": ["romance", "comedy", "romantic_comedy"],
    "love": ["romance", "love"], "date": ["romance"],
    "scifi": ["sci_fi"], "sci": ["sci_fi"], "science": ["sci_fi", "science"],
    "futuristic": ["sci_fi", "future"], "space": ["space", "sci_fi"],
    "alien": ["aliens", "alien"], "aliens": ["aliens", "alien"],
    "cartoon": ["animation"], "animated": ["animation"], "anime": ["anime", "animation"],
    "kids": ["kids", "family"], "children": ["kids", "family"], "family": ["family"],
    "superhero": ["superhero", "comic_book"], "superheroes": ["superhero", "comic_book"],
    "marvel": ["marvel", "superhero"], "dc": ["superhero", "comic_book"],
    "suspense": ["thriller", "suspense"], "suspenseful": ["thriller", "suspense"],
    "tense": ["thriller", "suspense"], "intense": ["thriller", "intense"],
    "exciting": ["action", "adventure"], "explosions": ["action"],
    "fight": ["action", "martial_arts"], "fighting": ["action", "martial_arts"],
    "kung": ["martial_arts"], "karate": ["martial_arts"],
    "detective": ["detective", "mystery"], "whodunnit": ["whodunit", "mystery"],
    "murder": ["murder_mystery", "serial_killer", "crime"],
    "mob": ["mafia", "gangster"], "mafia": ["mafia", "gangster"],
    "gangsters": ["gangster", "mafia"], "robbery": ["heist"], "heists": ["heist"],
    "twisty": ["twist", "mind_bending"], "twists": ["twist"],
    "confusing": ["mind_bending"], "mindbending": ["mind_bending"],
    "trippy": ["mind_bending", "surreal"], "cerebral": ["mind_bending", "philosophical"],
    "sad": ["tearjerker", "emotional", "drama"], "cry": ["tearjerker", "emotional"],
    "emotional": ["emotional", "tearjerker"], "heartwarming": ["heartwarming", "feel_good"],
    "uplifting": ["feel_good", "inspirational"], "happy": ["feel_good"],
    "wholesome": ["feel_good", "heartwarming", "family"],
    "inspiring": ["inspirational", "true_story"], "inspirational": ["inspirational"],
    "cowboy": ["western", "cowboy"], "cowboys": ["western", "cowboy"],
    "wild": ["western"], "soldiers": ["war", "soldiers"], "military": ["war", "soldiers"],
    "ww2": ["world_war_ii"], "wwii": ["world_war_ii"], "ww1": ["world_war_i"],
    "biopic": ["biography", "true_story"], "real": ["true_story"],
    "historical": ["history", "period"], "musical": ["musical", "singing"],
    "singing": ["singing", "musical"], "music": ["music", "musician"],
    "sports": ["sport"], "sport": ["sport"], "zombie": ["zombies"],
    "ghost": ["ghosts"], "haunted": ["haunted_house", "ghosts"],
    "robots": ["robot", "artificial_intelligence"], "ai": ["artificial_intelligence"],
    "dinosaur": ["dinosaurs"], "dragon": ["dragons"], "wizards": ["wizard"],
    "magic": ["magic", "wizard"], "christmas": ["christmas", "holiday"],
    "foreign": ["foreign_language"], "korean": ["korean"], "japanese": ["japanese", "japan"],
    "indian": ["india", "bollywood"], "bollywood": ["bollywood", "india"],
    "french": ["france"], "dark": ["dark"], "gritty": ["gritty", "dark"],
    "dystopian": ["dystopia"], "apocalyptic": ["post_apocalyptic"],
    "apocalypse": ["post_apocalyptic", "apocalypse"],
    "teen": ["teen", "high_school"], "teenage": ["teen", "coming_of_age"],
    "school": ["high_school", "school"], "cars": ["cars", "car_chase", "racing"],
    "racing": ["racing"], "spy": ["spy", "espionage"], "spies": ["spy", "espionage"],
    "assassin": ["assassin", "hitman"], "killer": ["serial_killer", "hitman"],
    "revenge": ["revenge"], "time": ["time"], "timetravel": ["time_travel"],
    "classic": ["classic"], "old": ["classic"], "indie": ["indie"],
    "quirky": ["quirky", "whimsical"], "epic": ["epic"],
}

GENRE_ALIASES = {"science fiction": "sci-fi", "scifi": "sci-fi", "romcom": "romance"}

DECADE_RE = re.compile(r"\b(?:(19|20)?(\d)0)'?s\b")
YEAR_RE = re.compile(r"\b(19[2-9]\d|20[0-4]\d)\b")

# Relative importance of each field when building a movie's document.
FIELD_WEIGHTS = {
    "genres": 3.0,
    "keywords": 2.5,
    "director": 2.0,
    "cast": 2.0,
    "title": 1.5,
    "synopsis": 1.0,
}


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _ascii(text).lower())


def _stem(word: str) -> str:
    """Tiny plural stemmer; good enough to match 'dreams'/'dream', 'heists'/'heist'."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _phrase_token(phrase: str) -> str:
    return "_".join(_words(phrase))


def tokenize_text(text: str) -> list[str]:
    return [_stem(w) for w in _words(text) if w not in STOPWORDS and len(w) > 1]


@dataclass
class Movie:
    id: str
    title: str
    year: int
    genres: list[str]
    director: str
    cast: list[str]
    runtime: int
    rating: float
    synopsis: str
    keywords: list[str]
    watch: list[str]
    vector: dict[str, float] = field(default_factory=dict, repr=False)

    @property
    def directors(self) -> list[str]:
        return [d.strip() for d in self.director.split(",") if d.strip()]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "year": self.year,
            "genres": self.genres,
            "director": self.director,
            "cast": self.cast,
            "runtime": self.runtime,
            "rating": self.rating,
            "synopsis": self.synopsis,
            "keywords": self.keywords,
            "watch": self.watch,
        }


def _normalize(vec: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(v * v for v in vec.values()))
    if norm == 0:
        return {}
    return {k: v / norm for k, v in vec.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


class Recommender:
    def __init__(self, movies: list[dict]):
        self.movies: list[Movie] = [Movie(**m) for m in movies]
        self.by_id: dict[str, Movie] = {m.id: m for m in self.movies}
        self._people: dict[str, str] = {}   # phrase token -> display name
        self._genres: dict[str, str] = {}   # phrase token -> display name
        self._build_index()

    @classmethod
    def from_file(cls, path: Path = DATA_PATH) -> "Recommender":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    # ------------------------------------------------------------------ index
    def _raw_terms(self, m: Movie) -> Counter:
        terms: Counter = Counter()
        w = FIELD_WEIGHTS
        for g in m.genres:
            tok = _phrase_token(g)
            self._genres[tok] = g
            terms[tok] += w["genres"]
        for kw in m.keywords:
            terms[_phrase_token(kw)] += w["keywords"]
            for t in tokenize_text(kw):
                terms[t] += w["keywords"] * 0.5
        for person, weight in [(p, w["director"]) for p in m.directors] + [
            (p, w["cast"]) for p in m.cast
        ]:
            tok = _phrase_token(person)
            self._people[tok] = person
            terms[tok] += weight
        for t in tokenize_text(m.title):
            terms[t] += w["title"]
        for t in tokenize_text(m.synopsis):
            terms[t] += w["synopsis"]
        return terms

    def _build_index(self) -> None:
        raw = {m.id: self._raw_terms(m) for m in self.movies}
        df: Counter = Counter()
        for terms in raw.values():
            df.update(terms.keys())
        n = len(self.movies)
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}
        for m in self.movies:
            vec = {t: (1 + math.log(tf)) * self.idf[t] if tf >= 1 else tf * self.idf[t]
                   for t, tf in raw[m.id].items()}
            m.vector = _normalize(vec)
        # Longest titles first so "the dark knight" wins over "the dark"
        self._titles = sorted(
            ((" ".join(_words(m.title)), m) for m in self.movies),
            key=lambda x: -len(x[0]),
        )

    # ------------------------------------------------------------ query parse
    def parse_query(self, query: str) -> dict:
        text = " ".join(_words(query))
        lowered = _ascii(query).lower()
        parsed = {
            "terms": Counter(),
            "people": [],
            "genres": [],
            "titles": [],
            "year_range": None,
        }

        # Decades ("90s", "1980s") and explicit years.
        dm = DECADE_RE.search(lowered)
        if dm:
            century, decade = dm.group(1), int(dm.group(2))
            if century:
                start = int(century + str(decade) + "0")
            else:
                start = (2000 if decade <= 2 else 1900) + decade * 10
            parsed["year_range"] = (start, start + 9)
        else:
            ym = YEAR_RE.search(lowered)
            if ym:
                y = int(ym.group(1))
                parsed["year_range"] = (y - 2, y + 2)

        # Referenced movie titles ("like inception", "similar to the matrix").
        remaining = f" {text} "
        for title_text, movie in self._titles:
            if len(title_text) < 4 and title_text not in {"up", "it", "us"}:
                continue
            needle = f" {title_text} "
            if needle in remaining and (len(title_text.split()) > 1 or re.search(
                    r"\b(like|similar|as|than)\s+" + re.escape(title_text) + r"\b", text)):
                parsed["titles"].append(movie)
                remaining = remaining.replace(needle, " ")

        words = remaining.split()
        # People: match full names as n-grams, and bare last names.
        used = set()
        for n in (3, 2):
            for i in range(len(words) - n + 1):
                tok = "_".join(words[i:i + n])
                if tok in self._people:
                    parsed["people"].append(self._people[tok])
                    parsed["terms"][tok] += 4.0
                    used.update(range(i, i + n))
        last_names = defaultdict(list)
        for tok, name in self._people.items():
            last_names[tok.split("_")[-1]].append(tok)
        for i, word in enumerate(words):
            if i in used or word in STOPWORDS or len(word) < 4:
                continue
            candidates = last_names.get(word, [])
            if 0 < len(candidates) <= 2 and word not in self.idf:
                for tok in candidates:
                    parsed["people"].append(self._people[tok])
                    parsed["terms"][tok] += 3.0
                used.add(i)

        rest = " ".join(w for i, w in enumerate(words) if i not in used)
        for alias, genre in GENRE_ALIASES.items():
            rest = rest.replace(alias, genre.replace("-", " "))

        # Multi-word phrases (keywords / genres) present in the catalog.
        rest_words = rest.split()
        for n in (3, 2):
            for i in range(len(rest_words) - n + 1):
                tok = "_".join(rest_words[i:i + n])
                if tok in self.idf:
                    parsed["terms"][tok] += 2.0
                    if tok in self._genres:
                        parsed["genres"].append(self._genres[tok])

        for word in rest_words:
            if word in STOPWORDS or len(word) < 2:
                continue
            expansions = SYNONYMS.get(word)
            if expansions:
                for e in expansions:
                    parsed["terms"][e] += 1.5
            stem = _stem(word)
            parsed["terms"][stem] += 1.0
            parsed["terms"][word] += 0.5
        for tok in list(parsed["terms"]):
            if tok in self._genres and self._genres[tok] not in parsed["genres"]:
                parsed["genres"].append(self._genres[tok])
        return parsed

    def _query_vector(self, terms: Counter) -> dict[str, float]:
        return _normalize({t: w * self.idf[t] for t, w in terms.items() if t in self.idf})

    # ------------------------------------------------------------- explaining
    def _reasons(self, movie: Movie, parsed: dict) -> list[str]:
        reasons = []
        people = [p for p in parsed["people"] if p in movie.cast or p in movie.directors]
        for p in dict.fromkeys(people):
            reasons.append(f"Directed by {p}" if p in movie.directors else f"Stars {p}")
        genres = [g for g in parsed["genres"] if g in movie.genres]
        if genres:
            reasons.append("Genre: " + ", ".join(dict.fromkeys(genres)))
        kw_hits = [k for k in movie.keywords if _phrase_token(k) in parsed["terms"]
                   or any(t in parsed["terms"] for t in tokenize_text(k))]
        if kw_hits:
            reasons.append("Themes: " + ", ".join(kw_hits[:3]))
        if parsed["titles"]:
            reasons.append("Similar to " + " & ".join(m.title for m in parsed["titles"][:2]))
        yr = parsed["year_range"]
        if yr and yr[0] <= movie.year <= yr[1]:
            reasons.append(f"From {movie.year}")
        return reasons[:4]

    # --------------------------------------------------------------- ranking
    @staticmethod
    def _quality(movie: Movie) -> float:
        return (movie.rating - 7.0) * 0.02

    def search(self, query: str, limit: int = 12, exclude: set[str] | None = None) -> dict:
        exclude = set(exclude or ())
        parsed = self.parse_query(query)
        exclude.update(m.id for m in parsed["titles"])

        qvec = self._query_vector(parsed["terms"])
        # Referenced titles contribute their whole vector (a "more like this" query).
        if parsed["titles"]:
            combo: Counter = Counter()
            for m in parsed["titles"]:
                for k, v in m.vector.items():
                    combo[k] += v
            for k, v in qvec.items():
                combo[k] += v * 1.2
            qvec = _normalize(dict(combo))

        yr = parsed["year_range"]
        candidates = [m for m in self.movies if m.id not in exclude]
        if yr:
            in_range = [m for m in candidates if yr[0] <= m.year <= yr[1]]
            if in_range:
                candidates = in_range

        def score_all(vec):
            scored = []
            for m in candidates:
                s = _cosine(vec, m.vector) if vec else 0.0
                # Hard boosts for explicit matches the user clearly asked for.
                if any(p in m.cast or p in m.directors for p in parsed["people"]):
                    s += 0.25
                if parsed["genres"]:
                    s += 0.05 * sum(g in m.genres for g in parsed["genres"])
                scored.append((s, m))
            return scored

        scored = score_all(qvec)
        relevant = [x for x in scored if x[0] > 0.02]
        ranked = sorted(relevant, key=lambda x: -(x[0] + self._quality(x[1])))[:limit]

        # Too few direct hits: pad with movies closest to the query plus the best
        # matches found so far (pseudo-relevance feedback), so the user always
        # gets a full page of related movies. Movies outside a requested decade
        # are allowed here, but in-range ones get a nudge.
        if len(ranked) < limit:
            chosen = {m.id for _, m in ranked}
            centroid: Counter = Counter({k: v * 1.5 for k, v in qvec.items()})
            for _, m in ranked[:3]:
                for k, v in m.vector.items():
                    centroid[k] += v
            cvec = _normalize(dict(centroid))
            pool = [m for m in self.movies if m.id not in exclude and m.id not in chosen]
            padding = []
            for m in pool:
                s = _cosine(cvec, m.vector) if cvec else 0.0
                if yr and yr[0] <= m.year <= yr[1]:
                    s += 0.05
                padding.append((s * 0.5, m))
            padding.sort(key=lambda x: -(x[0] + self._quality(x[1])))
            ranked += padding[: limit - len(ranked)]

        results = []
        for s, m in ranked:
            item = m.to_dict()
            item["score"] = round(s, 4)
            item["reasons"] = self._reasons(m, parsed)
            results.append(item)
        return {
            "query": query,
            "interpreted": {
                "people": list(dict.fromkeys(parsed["people"])),
                "genres": list(dict.fromkeys(parsed["genres"])),
                "similar_to": [m.title for m in parsed["titles"]],
                "years": list(yr) if yr else None,
            },
            "results": results,
        }

    def similar(self, movie_id: str, limit: int = 12) -> list[dict]:
        base = self.by_id[movie_id]
        scored = sorted(
            ((_cosine(base.vector, m.vector) + self._quality(m), m)
             for m in self.movies if m.id != movie_id),
            key=lambda x: -x[0],
        )[:limit]
        return [dict(m.to_dict(), score=round(s, 4)) for s, m in scored]

    def from_history(self, history: list[dict], limit: int = 12) -> list[dict]:
        """Recommend from watch history.

        ``history`` items: {"movie_id": str, "liked": bool | None}. Liked movies
        pull the profile toward them, disliked ones push it away, and more
        recent entries (later in the list) count slightly more.
        """
        entries = [h for h in history if h["movie_id"] in self.by_id]
        if not entries:
            return []
        profile: Counter = Counter()
        n = len(entries)
        for i, h in enumerate(entries):
            recency = 0.6 + 0.4 * (i + 1) / n
            liked = h.get("liked")
            weight = 1.3 if liked is True else -0.7 if liked is False else 1.0
            for k, v in self.by_id[h["movie_id"]].vector.items():
                profile[k] += v * weight * recency
        pvec = _normalize({k: v for k, v in profile.items() if v > 0})
        seen = {h["movie_id"] for h in entries}
        positives = [self.by_id[h["movie_id"]] for h in entries if h.get("liked") is not False]

        scored = []
        for m in self.movies:
            if m.id in seen:
                continue
            s = _cosine(pvec, m.vector)
            # Penalise closeness to disliked movies.
            for h in entries:
                if h.get("liked") is False:
                    s -= 0.3 * _cosine(self.by_id[h["movie_id"]].vector, m.vector)
            scored.append((s + self._quality(m), m))
        scored.sort(key=lambda x: -x[0])

        results = []
        for s, m in scored[:limit]:
            because = max(positives, key=lambda p: _cosine(p.vector, m.vector), default=None)
            item = m.to_dict()
            item["score"] = round(s, 4)
            item["reasons"] = [f"Because you watched {because.title}"] if because else []
            results.append(item)
        return results

    def lookup(self, text: str, limit: int = 8) -> list[dict]:
        """Title autocomplete."""
        q = " ".join(_words(text))
        if not q:
            return []
        hits = []
        for m in self.movies:
            t = " ".join(_words(m.title))
            if t.startswith(q):
                hits.append((0, m))
            elif q in t:
                hits.append((1, m))
        hits.sort(key=lambda x: (x[0], -x[1].rating))
        return [{"id": m.id, "title": m.title, "year": m.year} for _, m in hits[:limit]]
