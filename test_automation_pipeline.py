import unittest
import json
from tempfile import TemporaryDirectory
from pathlib import Path

from automation_pipeline import (
    INSTAGRAM_CAROUSEL_TARGET,
    INSTAGRAM_REELS_TARGET,
    INSTAGRAM_STORIES_TARGET,
    YOUTUBE_SHORTS_TARGET,
    all_targets_published,
    build_requested_publish_keys,
    clear_directory_contents,
    normalize_state,
    purge_old_files,
)
from twitter_timeline_scraper import load_processed_tweet_ids


class AutomationPipelineHelpersTest(unittest.TestCase):
    def test_normalize_state_preserves_legacy_publish_list(self) -> None:
        state = normalize_state({"published_outputs": ["video_a.mp4"]})
        self.assertEqual(state["published_outputs_by_target"][INSTAGRAM_REELS_TARGET], ["video_a.mp4"])
        self.assertEqual(state["published_outputs_by_target"][INSTAGRAM_STORIES_TARGET], [])
        self.assertEqual(state["published_outputs_by_target"][INSTAGRAM_CAROUSEL_TARGET], ["video_a.mp4"])
        self.assertEqual(state["published_outputs_by_target"][YOUTUBE_SHORTS_TARGET], [])
        self.assertEqual(state["published_outputs"], ["video_a.mp4"])

    def test_normalize_state_migrates_legacy_instagram_target(self) -> None:
        state = normalize_state({"published_outputs_by_target": {"instagram": ["video_a.mp4"]}})
        self.assertEqual(state["published_outputs_by_target"][INSTAGRAM_REELS_TARGET], ["video_a.mp4"])
        self.assertEqual(state["published_outputs_by_target"][INSTAGRAM_CAROUSEL_TARGET], ["video_a.mp4"])
        self.assertEqual(state["published_outputs_by_target"][INSTAGRAM_STORIES_TARGET], [])
        self.assertEqual(state["published_outputs_by_target"][YOUTUBE_SHORTS_TARGET], [])

    def test_normalize_state_preserves_youtube_shorts_target(self) -> None:
        state = normalize_state(
            {"published_outputs_by_target": {YOUTUBE_SHORTS_TARGET: ["video_a.mp4"]}}
        )

        self.assertEqual(state["published_outputs_by_target"][YOUTUBE_SHORTS_TARGET], ["video_a.mp4"])
        self.assertEqual(state["published_outputs"], ["video_a.mp4"])

    def test_build_requested_publish_keys_supports_reels_and_stories(self) -> None:
        self.assertEqual(
            build_requested_publish_keys("both"),
            [INSTAGRAM_REELS_TARGET, INSTAGRAM_STORIES_TARGET],
        )

    def test_all_targets_published_requires_reel_and_story(self) -> None:
        published = {
            INSTAGRAM_REELS_TARGET: {"video_final.mp4"},
            INSTAGRAM_STORIES_TARGET: set(),
        }
        requested = [INSTAGRAM_REELS_TARGET, INSTAGRAM_STORIES_TARGET]

        self.assertFalse(
            all_targets_published("video_final.mp4", published, requested)
        )

        published[INSTAGRAM_STORIES_TARGET].add("video_final.mp4")
        self.assertTrue(
            all_targets_published("video_final.mp4", published, requested)
        )

    def test_clear_directory_contents_removes_files_and_subdirectories(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            target_dir = project_dir / "outputs_ig"
            nested_dir = target_dir / "carousels"
            nested_dir.mkdir(parents=True)
            (target_dir / "video.mp4").write_bytes(b"video")
            (nested_dir / "slide.png").write_bytes(b"image")

            removed = clear_directory_contents(target_dir, project_dir)

            self.assertEqual(removed, 2)
            self.assertTrue(target_dir.is_dir())
            self.assertEqual(list(target_dir.iterdir()), [])

    def test_clear_directory_contents_dry_run_preserves_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            target_dir = project_dir / "timeline_downloads"
            target_dir.mkdir()
            source_file = target_dir / "source.mp4"
            source_file.write_bytes(b"video")

            removed = clear_directory_contents(target_dir, project_dir, dry_run=True)

            self.assertEqual(removed, 1)
            self.assertTrue(source_file.exists())

    def test_clear_directory_contents_refuses_project_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)

            with self.assertRaises(ValueError):
                clear_directory_contents(project_dir, project_dir)

    def test_load_processed_tweet_ids_ignores_legacy_analytics_names(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "processed_sources": [
                            "creator_2067720159784301004.mp4",
                            "legacy_analytics.mp4",
                        ]
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                load_processed_tweet_ids(state_file),
                {"2067720159784301004"},
            )

    def test_purge_old_files_removes_old_files(self) -> None:
        import os
        import time

        with TemporaryDirectory() as temp_dir:
            target_dir = Path(temp_dir)
            old_file = target_dir / "old_log.txt"
            new_file = target_dir / "new_log.txt"

            old_file.write_text("old", encoding="utf-8")
            new_file.write_text("new", encoding="utf-8")

            # Simular arquivo antigo alterando mtime para 10 dias atras
            ten_days_ago = time.time() - (10 * 86400)
            os.utime(old_file, (ten_days_ago, ten_days_ago))

            removed = purge_old_files(target_dir, max_age_days=7)

            self.assertEqual(removed, 1)
            self.assertFalse(old_file.exists())
            self.assertTrue(new_file.exists())


if __name__ == "__main__":
    unittest.main()
