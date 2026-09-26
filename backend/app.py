"""ReelMatch API server.

Run:  python backend/app.py      (serves the API and the frontend on :5000)
"""

from __future__ import annotations

import os
import re
import sqlite3
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory

from recommender import Recommender
from tmdb import TMDB

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DB_PATH = Path(os.environ.get("REELMATCH_DB", ROOT / "backend" / "data" / "reelmatch.db"))

app = Flask(__name__, static_folder=None)
recommender = Recommender.from_file()
tmdb = TMDB()
_pool = ThreadPoolExecutor(max_workers=8)

USER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ------------------------------------------------------------------ storage
def db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS history (
                   user_id    TEXT NOT NULL,
                   movie_id   TEXT NOT NULL,
                   liked      INTEGER,            -- 1 liked, 0 disliked, NULL neutral
                   watched_at TEXT NOT NULL DEFAULT (datetime('now')),
                   PRIMARY KEY (user_id, movie_id)
               )"""
        )


def current_user() -> str:
    user = request.headers.get("X-User-Id") or request.args.get("user") or "default"
    if not USER_RE.match(user):
        user = "default"
    return user


def load_history(user: str) -> list[dict]:
    rows = db().execute(
        "SELECT movie_id, liked, watched_at FROM history WHERE user_id = ? ORDER BY watched_at, rowid",
        (user,),
    ).fetchall()
    return [
        {"movie_id": r["movie_id"],
         "liked": None if r["liked"] is None else bool(r["liked"]),
         "watched_at": r["watched_at"]}
        for r in rows
    ]


# ---------------------------------------------------------------- enriching
def where_to_watch(movie: dict, live: dict | None) -> dict:
    """Streaming info: live TMDB/JustWatch data when available, else curated."""
    q = urllib.parse.quote(f"{movie['title']} {movie['year']}")
    justwatch = f"https://www.justwatch.com/us/search?q={urllib.parse.quote(movie['title'])}"
    if live and live.get("providers"):
        p = live["providers"]
        return {
            "source": "live",
            "stream": [x["name"] for x in p.get("flatrate", [])],
            "free": [x["name"] for x in p.get("free", []) + p.get("ads", [])],
            "rent": [x["name"] for x in p.get("rent", [])],
            "buy": [x["name"] for x in p.get("buy", [])],
            "link": p.get("link") or justwatch,
        }
    return {
        "source": "catalog",
        "stream": movie["watch"],
        "free": [],
        "rent": ["Apple TV", "Prime Video", "Google Play"],
        "buy": [],
        "link": justwatch,
        "search": f"https://www.google.com/search?q=watch+{q}",
    }


def enrich(movies: list[dict]) -> list[dict]:
    lives = list(_pool.map(lambda m: tmdb.details(m["title"], m["year"]), movies)) \
        if tmdb.enabled else [None] * len(movies)
    out = []
    for m, live in zip(movies, lives):
        m = dict(m)
        m["poster"] = live["poster"] if live else None
        m["where_to_watch"] = where_to_watch(m, live)
        out.append(m)
    return out


def _limit() -> int:
    try:
        return max(1, min(int(request.args.get("limit", 12)), 30))
    except ValueError:
        return 12


# ----------------------------------------------------------------- routes
@app.get("/api/health")
def health():
    return jsonify(ok=True, movies=len(recommender.movies), live_data=tmdb.enabled)


@app.get("/api/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify(error="Missing query parameter 'q'"), 400
    exclude = set()
    if request.args.get("hide_watched") == "1":
        exclude = {h["movie_id"] for h in load_history(current_user())}
    res = recommender.search(q, limit=_limit(), exclude=exclude)
    res["results"] = enrich(res["results"])
    return jsonify(res)


@app.get("/api/movies")
def list_movies():
    q = request.args.get("q", "")
    if q:
        return jsonify(recommender.lookup(q))
    return jsonify([{"id": m.id, "title": m.title, "year": m.year} for m in recommender.movies])


@app.get("/api/movies/<movie_id>")
def movie_detail(movie_id: str):
    m = recommender.by_id.get(movie_id)
    if not m:
        return jsonify(error="Movie not found"), 404
    [movie] = enrich([m.to_dict()])
    movie["similar"] = enrich(recommender.similar(movie_id, limit=6))
    return jsonify(movie)


@app.get("/api/trending")
def trending():
    top = sorted(recommender.movies, key=lambda m: (-m.rating, -m.year))[:_limit()]
    return jsonify(enrich([m.to_dict() for m in top]))


@app.get("/api/history")
def get_history():
    hist = load_history(current_user())
    by_id = recommender.by_id
    items = [dict(by_id[h["movie_id"]].to_dict(), liked=h["liked"], watched_at=h["watched_at"])
             for h in reversed(hist) if h["movie_id"] in by_id]
    return jsonify(enrich(items))


@app.post("/api/history")
def add_history():
    data = request.get_json(silent=True) or {}
    movie_id = data.get("movie_id")
    if movie_id not in recommender.by_id:
        return jsonify(error="Unknown movie_id"), 400
    liked = data.get("liked")
    liked_val = None if liked is None else int(bool(liked))
    conn = db()
    conn.execute(
        """INSERT INTO history (user_id, movie_id, liked) VALUES (?, ?, ?)
           ON CONFLICT(user_id, movie_id) DO UPDATE SET liked = excluded.liked""",
        (current_user(), movie_id, liked_val),
    )
    conn.commit()
    return jsonify(ok=True), 201


@app.delete("/api/history/<movie_id>")
def delete_history(movie_id: str):
    conn = db()
    conn.execute("DELETE FROM history WHERE user_id = ? AND movie_id = ?", (current_user(), movie_id))
    conn.commit()
    return jsonify(ok=True)


@app.get("/api/recommendations")
def history_recommendations():
    hist = load_history(current_user())
    recs = recommender.from_history(hist, limit=_limit())
    return jsonify({"based_on": len(hist), "results": enrich(recs)})


# --------------------------------------------------------------- frontend
@app.get("/")
def index():
    return send_from_directory(FRONTEND, "index.html")


@app.get("/<path:path>")
def static_files(path: str):
    return send_from_directory(FRONTEND, path)


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
