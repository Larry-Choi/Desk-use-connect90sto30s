import json
import tempfile
import unittest
from pathlib import Path

from desk.library import (
    LibraryError,
    add_app,
    counts,
    delete_app,
    discover,
    empty_library,
    ensure_shape,
    launch_target,
    load_or_create,
    merge_discovered,
    save,
    update_app,
)


class LibraryTests(unittest.TestCase):
    def test_default_categories_do_not_invent_all(self):
        data = empty_library()
        titles = [item["title"] for item in data["categories"]]
        self.assertEqual(titles, ["AI 작업실", "영상 편집실", "도구 서랍"])
        self.assertNotIn("전체", titles)

    def test_counts_stay_separate(self):
        data = empty_library()
        data["apps"] = [
            _app("a", "ai", favorite=True, position=0),
            _app("b", "ai", favorite=False, position=1),
            _app("c", "tools", favorite=True, position=2),
        ]
        summary = counts(data)
        self.assertEqual(summary["registered"], 3)
        self.assertEqual(summary["favorites"], 2)
        self.assertEqual(summary["byCategory"]["ai"], 2)
        self.assertEqual(summary["favoritesByCategory"]["ai"], 1)
        self.assertEqual(summary["byCategory"]["video"], 0)
        self.assertNotIn("all", summary["byCategory"])
        self.assertEqual(summary["favoritesByCategory"]["tools"], 1)

    def test_category_named_all_is_dropped(self):
        data = ensure_shape(
            {
                "categories": [{"id": "all", "title": "전체", "position": 0}],
                "apps": [],
            }
        )
        self.assertNotIn("전체", [item["title"] for item in data["categories"]])
        self.assertEqual(data["categories"][0]["title"], "AI 작업실")

    def test_web_address_is_rejected(self):
        data = empty_library()
        with self.assertRaises(LibraryError):
            add_app(
                data,
                {
                    "name": "사이트",
                    "categoryId": "tools",
                    "target": "https://example.com",
                    "favorite": True,
                },
            )

    def test_round_trip_and_missing_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "Demo.exe"
            target.write_bytes(b"")
            path = root / "library.json"
            catalog = [
                {"name": "Demo", "categoryId": "ai", "paths": [target]},
                {"name": "Gone", "categoryId": "video", "paths": [root / "missing.exe"]},
            ]
            data = load_or_create(path, catalog)
            self.assertEqual([app["name"] for app in data["apps"]], ["Demo"])
            self.assertEqual(counts(data)["byCategory"]["video"], 0)
            again = load_or_create(path, catalog)
            self.assertEqual(len(again["apps"]), 1)
            added = merge_discovered(again, discover(catalog))
            self.assertEqual(added, [])
            update_app(
                again,
                again["apps"][0]["id"],
                {
                    "name": "Demo 2",
                    "categoryId": "tools",
                    "target": str(target),
                    "favorite": False,
                },
            )
            save(path, again)
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stored["apps"][0]["name"], "Demo 2")
            self.assertFalse(stored["apps"][0]["favorite"])
            delete_app(stored, stored["apps"][0]["id"])
            self.assertEqual(stored["apps"], [])

        with self.assertRaises(LibraryError):
            launch_target(r"Z:\desk-missing.exe")


def _app(name, category_id, favorite, position):
    return {
        "id": name,
        "name": name,
        "categoryId": category_id,
        "target": rf"C:\apps\{name}.exe",
        "favorite": favorite,
        "position": position,
    }


if __name__ == "__main__":
    unittest.main()
