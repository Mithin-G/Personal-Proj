"""Tests for the live TMDB engine, run against the offline FakeTMDB."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engines import LiveEngine  # noqa: E402
from fake_tmdb import FakeTMDB  # noqa: E402
from recommender import Recommender  # noqa: E402
from tmdb import TMDB  # noqa: E402


def make_engine():
    fake = FakeTMDB()
    client = TMDB(api_key="test", fetch=fake)
    return LiveEngine(client, Recommender.from_file()), fake


class LiveEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine, self.fake = make_engine()

    def titles(self, q, **kw):
        return [m["title"] for m in self.engine.search(q, limit=12, exclude=set(), **kw)["results"]]

    def test_results_have_live_fields(self):
        res = self.engine.search("Tom Hanks", 12, set())
        self.assertEqual(len(res["results"]), 12)
        m = res["results"][0]
        self.assertTrue(m["id"].startswith("tmdb-"))
        self.assertTrue(m["poster"].startswith("https://image.tmdb.org/"))
        self.assertEqual(m["where_to_watch"]["source"], "live")
        self.assertIn("Tom Hanks", m["cast"])
        self.assertIn("Tom Hanks", res["interpreted"]["people"])

    def test_person_lookup_uses_discover(self):
        self.engine.search("christopher nolan", 12, set())
        self.assertTrue(any(p == "/discover/movie" and "with_people" in q for p, q in self.fake.calls))
        top = self.engine.search("christopher nolan", 12, set())["results"][:5]
        self.assertTrue(all("Christopher Nolan" in m["director"] for m in top))

    def test_movies_like(self):
        res = self.engine.search("movies like Inception", 12, set())
        self.assertEqual(res["interpreted"]["similar_to"], ["Inception"])
        titles = [m["title"] for m in res["results"]]
        self.assertNotIn("Inception", titles)
        self.assertTrue({"Tenet", "Memento", "The Matrix", "Shutter Island"} & set(titles[:6]))

    def test_keyword_query(self):
        self.assertEqual(self.titles("dinosaurs")[0], "Jurassic Park")
        self.assertIn("Train to Busan", self.titles("zombies")[:3])

    def test_genre_and_decade(self):
        res = self.engine.search("scary 80s horror", 12, set())
        self.assertEqual(res["interpreted"]["years"], [1980, 1989])
        first = res["results"][0]
        self.assertIn("Horror", first["genres"])
        self.assertTrue(1980 <= first["year"] <= 1989)

    def test_exclude_watched(self):
        jp = f"tmdb-{self.fake.tid['jurassic-park-1993']}"
        titles = [m["title"] for m in self.engine.search("dinosaurs", 12, {jp})["results"]]
        self.assertNotIn("Jurassic Park", titles)

    def test_history_recommendations_and_legacy_ids(self):
        conj = f"tmdb-{self.fake.tid['the-conjuring-2013']}"
        recs = self.engine.recommend([
            {"movie_id": conj, "liked": True},
            {"movie_id": "get-out-2017", "liked": None},   # bundled-catalog id gets resolved
        ], 12)
        self.assertEqual(len(recs), 12)
        titles = {m["title"] for m in recs}
        self.assertNotIn("The Conjuring", titles)
        self.assertNotIn("Get Out", titles)
        self.assertIn("Horror", recs[0]["genres"])
        self.assertTrue(recs[0]["reasons"][0].startswith("Because you watched"))

    def test_detail_and_lookup(self):
        tid = self.fake.tid["inception-2010"]
        movie = self.engine.get(f"tmdb-{tid}")
        self.assertEqual(movie["title"], "Inception")
        self.assertEqual(movie["where_to_watch"]["stream"], ["Netflix"])
        self.assertEqual(len(movie["similar"]), 6)
        self.assertEqual(self.engine.lookup("incep")[0]["title"], "Inception")

    def test_responses_are_cached(self):
        self.engine.search("Tom Hanks", 12, set())
        n = len(self.fake.calls)
        self.engine.search("Tom Hanks", 12, set())
        self.assertEqual(len(self.fake.calls), n)


class AppWithLiveEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["REELMATCH_DB"] = os.path.join(cls.tmp.name, "t.db")
        import app as app_module
        cls.app_module = app_module
        app_module.DB_PATH = Path(os.environ["REELMATCH_DB"])
        app_module.init_db()

    def setUp(self):
        self.engine, self.fake = make_engine()
        self.app_module.engine = self.engine
        self.client = self.app_module.app.test_client()
        self.h = {"X-User-Id": "live-test"}

    def test_flow(self):
        res = self.client.get("/api/search?q=space%20survival", headers=self.h).get_json()
        self.assertEqual(len(res["results"]), 12)
        first = res["results"][0]["id"]
        r = self.client.post("/api/history", json={"movie_id": first, "liked": True}, headers=self.h)
        self.assertEqual(r.status_code, 201)
        hist = self.client.get("/api/history", headers=self.h).get_json()
        self.assertEqual(hist[0]["id"], first)
        recs = self.client.get("/api/recommendations", headers=self.h).get_json()
        self.assertEqual(len(recs["results"]), 12)
        hidden = self.client.get("/api/search?q=space%20survival&hide_watched=1", headers=self.h).get_json()
        self.assertNotIn(first, [m["id"] for m in hidden["results"]])
        self.assertEqual(self.client.get("/api/health").get_json()["source"], "tmdb")

    def test_falls_back_to_catalog_when_tmdb_down(self):
        self.fake.fail = True
        res = self.client.get("/api/search?q=Tom%20Hanks", headers=self.h).get_json()
        self.assertEqual(len(res["results"]), 12)
        self.assertEqual(res["results"][0]["where_to_watch"]["source"], "catalog")


if __name__ == "__main__":
    unittest.main()
