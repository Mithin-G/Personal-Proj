import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from recommender import Recommender  # noqa: E402


class RecommenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = Recommender.from_file()

    def titles(self, query, **kw):
        return [m["title"] for m in self.r.search(query, **kw)["results"]]

    def test_always_returns_a_full_page(self):
        for q in ["dinosaurs", "xyzzy", "scary 80s horror", "Tom Hanks"]:
            self.assertEqual(len(self.r.search(q, limit=12)["results"]), 12, q)

    def test_actor_query(self):
        res = self.r.search("Keanu Reeves")
        self.assertIn("Keanu Reeves", res["interpreted"]["people"])
        top3 = [m["cast"] for m in res["results"][:3]]
        self.assertTrue(all("Keanu Reeves" in c for c in top3))

    def test_director_query(self):
        top = self.r.search("christopher nolan")["results"][:5]
        self.assertTrue(all("Christopher Nolan" in m["director"] for m in top))

    def test_genre_and_decade(self):
        res = self.r.search("scary 80s horror")
        self.assertEqual(res["interpreted"]["years"], [1980, 1989])
        self.assertIn("Horror", res["results"][0]["genres"])
        self.assertTrue(1980 <= res["results"][0]["year"] <= 1989)

    def test_movies_like_title_excludes_itself(self):
        res = self.r.search("movies like Inception")
        self.assertEqual(res["interpreted"]["similar_to"], ["Inception"])
        self.assertNotIn("Inception", [m["title"] for m in res["results"]])

    def test_description_query(self):
        self.assertIn("Jurassic Park", self.titles("dinosaurs")[:1])
        self.assertIn("Train to Busan", self.titles("zombie")[:3])

    def test_exclude(self):
        self.assertNotIn("Jurassic Park", self.titles("dinosaurs", exclude={"jurassic-park-1993"}))

    def test_history_recommendations(self):
        hist = [{"movie_id": "the-conjuring-2013", "liked": True},
                {"movie_id": "get-out-2017", "liked": None}]
        recs = self.r.from_history(hist)
        self.assertEqual(len(recs), 12)
        ids = {m["id"] for m in recs}
        self.assertNotIn("the-conjuring-2013", ids)
        self.assertIn("Horror", recs[0]["genres"])
        self.assertTrue(recs[0]["reasons"][0].startswith("Because you watched"))

    def test_disliked_pushes_away(self):
        liked_only = self.r.from_history([{"movie_id": "toy-story-1995", "liked": True}])
        with_dislike = self.r.from_history([
            {"movie_id": "toy-story-1995", "liked": True},
            {"movie_id": "finding-nemo-2003", "liked": False},
        ])
        rank = lambda recs, t: [m["title"] for m in recs].index(t) if t in [m["title"] for m in recs] else 99
        self.assertGreaterEqual(rank(with_dislike, "Up"), rank(liked_only, "Up"))

    def test_lookup(self):
        self.assertEqual(self.r.lookup("incep")[0]["title"], "Inception")


if __name__ == "__main__":
    unittest.main()
