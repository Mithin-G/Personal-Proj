# ReelMatch – Movie Recommendation Engine

Type any description — a genre, mood, plot idea, actor, director, decade, or
"movies like …" — and get 12 recommendations with a synopsis and where to
watch each one. Mark movies as watched (and 👍 / 👎 them) to get personalised
"Recommended for you" picks.

The frontend has a dark, IMDb-style layout. The backend is a small Flask API
with a content-based recommender written in pure Python.

## Quick start

```bash
pip install -r requirements.txt
python backend/app.py
# open http://localhost:5000
```

### Search every movie with the TMDB API (recommended)

By default the app searches a bundled catalog of about 250 movies. To search
**all movies** on [The Movie Database (TMDB)](https://www.themoviedb.org) and
get real posters and current streaming availability:

1. Create a free TMDB account and request an API key at
   <https://www.themoviedb.org/settings/api>. Choose "Developer" and
   "personal use".
2. Copy `.env.example` to `.env` and paste in your key. The v3 "API Key" and
   the v4 "API Read Access Token" both work. `.env` is git-ignored, so the key
   stays out of the repo.

```bash
cp .env.example .env      # then edit .env: TMDB_API_KEY=...
python backend/app.py
```

You can also pass it inline: `TMDB_API_KEY=your_key python backend/app.py`.

The footer confirms it with "Searching all movies on TMDB". `WATCH_REGION` is
a two-letter country code for the streaming providers (US, GB, IN, CA, …). If
TMDB is unreachable, the app falls back to the bundled catalog automatically.

**How live search works:** the query is broken into TMDB lookups. Names go to
person search, and plot or mood words go to keyword search. Genres and decades
become `discover` filters, and "movies like X" pulls TMDB's recommendations
for X. Up to 40 candidates are fetched with credits, keywords and watch
providers, then ranked by the same content-based recommender. History
recommendations combine TMDB's recommendations for each watched movie with
your like/dislike profile. API responses are cached for 6 hours.

## Example queries

| Query | What it does |
|---|---|
| `mind-bending sci-fi with a twist` | genre + themes |
| `Tom Hanks` / `keanu reeves action` | actor, optionally with a genre |
| `Christopher Nolan` | director |
| `movies like Inception` | similar to a known title |
| `scary 80s horror` | mood + decade filter |
| `sad movie that will make me cry` | mood words mapped to themes |
| `a movie about dinosaurs` | plot or description keywords |

## How it works

`backend/recommender.py`

- **Movie vectors:** each movie becomes a TF-IDF vector over its genres,
  keywords, cast, director, title and synopsis. Each field has its own weight;
  genres and keywords count most. Multi-word names and phrases such as
  `tom_hanks` and `time_travel` are kept as single tokens.
- **Query parsing:** free text is read for people (full names or unique last
  names), genres, catalog phrases, known titles ("like X"), and decades or
  years. Everyday words are expanded through a synonym table (`scary` → horror,
  `funny` → comedy, `cartoon` → animation, …).
- **Ranking:** results are ranked by cosine similarity, with boosts for
  explicit person and genre matches and a small tie-breaker for rating. When a
  query has few direct matches, the list is filled out with movies close to
  the best matches (pseudo-relevance feedback), so you always get a full page.
- **History recommendations:** your watched movies are combined into a taste
  profile. Liked movies count more and disliked ones push results away. Recent
  movies count slightly more. Each pick says "Because you watched X".

Watch history is stored in SQLite (`backend/data/reelmatch.db`). Each browser
gets its own anonymous user ID, sent in the `X-User-Id` header.

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/search?q=…&limit=12&hide_watched=1` | Free-text recommendations |
| GET | `/api/recommendations?limit=12` | Picks based on watch history |
| GET | `/api/history` | Watched movies |
| POST | `/api/history` `{movie_id, liked}` | Add or update a watched movie (`liked`: true/false/null) |
| DELETE | `/api/history/<movie_id>` | Remove from history |
| GET | `/api/movies/<id>` | Details, where to watch, similar movies |
| GET | `/api/movies?q=incep` | Title autocomplete |
| GET | `/api/trending` | Top-rated picks |

## Tests

```bash
python -m unittest discover -s tests
```

`tests/fake_tmdb.py` is an offline stand-in for the TMDB API, so the live
engine is tested end to end without a key or network access.

## Extending the catalog

Add entries to `backend/data/movies.json` with the fields `id`, `title`,
`year`, `genres`, `director`, `cast`, `runtime`, `rating`, `synopsis`,
`keywords` and `watch`. The index is rebuilt at startup.
