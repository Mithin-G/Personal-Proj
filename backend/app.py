"""ReelMatch API server.

Run:  python backend/app.py      (serves the API and the frontend on :5000)
Set TMDB_API_KEY to search every movie on TMDB instead of the bundled catalog.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory

from engines import LiveEngine, LocalEngine
from recommender import Recommender
from tmdb import TMDB, TMDBError

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DB_PATH = Path(os.environ.get("REELMATCH_DB", ROOT / "backend" / "data" / "reelmatch.db"))

app = Flask(__name__, static_folder=None)
recommender = Recommender.from_file()
tmdb = TMDB()
local_engine = LocalEngine(recommender)
# With a TMDB key, search the full TMDB movie database; otherwise the bundled catalog.
engine = LiveEngine(tmdb, recommender) if tmdb.enabled else local_engine

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


# ------------------------------------------------------------------ engine
def with_fallback(fn):
    """Run ``fn(engine)`` on the active engine; on a TMDB outage, use the local catalog."""
    try:
        return fn(engine)
    except TMDBError as e:
        app.logger.warning("TMDB unavailable, using bundled catalog: %s", e)
        return fn(local_engine)


def _limit() -> int:
    try:
        return max(1, min(int(request.args.get("limit", 12)), 30))
    except ValueError:
        return 12


# ----------------------------------------------------------------- routes
@app.get("/api/health")
def health():
    return jsonify(ok=True, source=engine.name, live_data=engine is not local_engine,
                   movies=len(recommender.movies) if engine is local_engine else None)


@app.get("/api/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify(error="Missing query parameter 'q'"), 400
    watched_ids = []
    if request.args.get("hide_watched") == "1":
        watched_ids = [h["movie_id"] for h in load_history(current_user())]

    def run(e):
        exclude = set(watched_ids) | {r for r in map(e.resolve, watched_ids) if r}
        return e.search(q, limit=_limit(), exclude=exclude)
    return jsonify(with_fallback(run))


@app.get("/api/movies")
def list_movies():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    return jsonify(with_fallback(lambda e: e.lookup(q)))


@app.get("/api/movies/<movie_id>")
def movie_detail(movie_id: str):
    movie = with_fallback(lambda e: e.get(movie_id))
    if not movie:
        return jsonify(error="Movie not found"), 404
    return jsonify(movie)


@app.get("/api/trending")
def trending():
    return jsonify(with_fallback(lambda e: e.trending(_limit())))


@app.get("/api/history")
def get_history():
    hist = list(reversed(load_history(current_user())))
    found = with_fallback(lambda e: e.get_many([h["movie_id"] for h in hist]))
    items = []
    for h in hist:
        m = found.get(h["movie_id"])
        if m:
            # Keep the stored id so like/remove actions hit the same history row.
            items.append(dict(m, id=h["movie_id"], liked=h["liked"], watched_at=h["watched_at"]))
    return jsonify(items)


@app.post("/api/history")
def add_history():
    data = request.get_json(silent=True) or {}
    movie_id = data.get("movie_id")
    if not isinstance(movie_id, str) or not (engine.valid_id(movie_id) or local_engine.valid_id(movie_id)):
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
    recs = with_fallback(lambda e: e.recommend(hist, limit=_limit()))
    return jsonify({"based_on": len(hist), "results": recs})


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
