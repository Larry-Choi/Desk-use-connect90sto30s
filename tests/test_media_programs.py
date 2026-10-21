import unittest

from desk.media import PAUSED, PLAYING, choose, classify
from desk.programs import collect_shortcuts, include_name


class MediaChoiceTests(unittest.TestCase):
    def test_spotify_is_not_the_desk_player(self):
        chosen = choose(
            [
                {"aumid": "Spotify", "status": PLAYING, "titles": []},
                {"aumid": "Chrome", "status": PAUSED, "titles": [], "youtube_window": True},
            ]
        )
        self.assertEqual(chosen["source"], "YouTube")
        self.assertEqual(chosen["status"], PAUSED)

    def test_playing_browser_beats_paused_one(self):
        chosen = choose(
            [
                {"aumid": "MSEdge", "status": PAUSED, "titles": []},
                {"aumid": "Chrome", "status": PLAYING, "titles": []},
            ]
        )
        self.assertEqual(chosen["source"], "Chrome")
        self.assertEqual(chosen["status"], PLAYING)

    def test_youtube_app_id_is_youtube(self):
        kind = classify("YouTube.YouTubeMusic", [])
        self.assertTrue(kind["youtube"])
        self.assertEqual(kind["source"], "YouTube")

    def test_unknown_app_is_left_alone(self):
        self.assertIsNone(classify("Spotify", ["Daily Mix"]))


class ShortcutFilterTests(unittest.TestCase):
    def test_uninstall_names_are_skipped(self):
        self.assertFalse(include_name("Uninstall Cursor"))
        self.assertFalse(include_name("프로그램 제거"))
        self.assertTrue(include_name("Cursor"))

    def test_duplicate_names_keep_their_folder(self):
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            one = root / "Alpha" / "Tool.lnk"
            two = root / "Beta" / "Tool.lnk"
            one.parent.mkdir()
            two.parent.mkdir()
            one.write_bytes(b"")
            two.write_bytes(b"")
            names = [item["name"] for item in collect_shortcuts([root])]
        self.assertEqual(names, ["Tool (Alpha)", "Tool (Beta)"])


if __name__ == "__main__":
    unittest.main()
