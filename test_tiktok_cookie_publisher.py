import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from tiktok_cookie_publisher import (
    publish_tiktok_cookie,
    resolve_cookies_file,
)


class TikTokCookiePublisherTest(unittest.TestCase):
    def test_resolve_cookies_file_custom_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cookie_file = Path(temp_dir) / "custom_cookies.txt"
            cookie_file.write_text("cookie_content", encoding="utf-8")

            resolved = resolve_cookies_file(str(cookie_file))
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved, cookie_file.resolve())

    def test_resolve_cookies_file_returns_none_when_missing(self) -> None:
        resolved = resolve_cookies_file("non_existent_cookie_file_123.txt")
        self.assertIsNone(resolved)

    def test_publish_tiktok_cookie_dry_run(self) -> None:
        with TemporaryDirectory() as temp_dir:
            video_file = Path(temp_dir) / "sample_video.mp4"
            video_file.write_bytes(b"dummy video content")

            cookie_file = Path(temp_dir) / "tiktok_cookies.txt"
            cookie_file.write_text("cookie_content", encoding="utf-8")

            success = publish_tiktok_cookie(
                video_path=video_file,
                description="Test description",
                cookies_path=cookie_file,
                dry_run=True,
            )
            self.assertTrue(success)

    def test_publish_tiktok_cookie_fails_when_video_missing(self) -> None:
        success = publish_tiktok_cookie(
            video_path="non_existent_video.mp4",
            dry_run=True,
        )
        self.assertFalse(success)


if __name__ == "__main__":
    unittest.main()
