import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from youtube_shorts_publisher import (
    build_video_resource,
    ensure_shorts_hashtag,
    parse_tags,
)


class YouTubeShortsPublisherHelpersTest(unittest.TestCase):
    def test_parse_tags_adds_shorts_once(self) -> None:
        self.assertEqual(
            parse_tags("IA,#Shorts, tecnologia, IA"),
            ["IA", "Shorts", "tecnologia"],
        )

    def test_ensure_shorts_hashtag_appends_to_description(self) -> None:
        self.assertEqual(
            ensure_shorts_hashtag("Titulo", "Descricao base", enabled=True),
            "Descricao base\n\n#Shorts",
        )

    def test_build_video_resource_uses_related_info_for_title_and_description(self) -> None:
        with TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "demo_final.mp4"
            info_path = Path(temp_dir) / "demo_info.txt"
            video_path.write_bytes(b"video")
            info_path.write_text(
                "Autor: alguem\nDescricao: Automacao com IA para tarefas repetitivas",
                encoding="utf-8",
            )

            resource = build_video_resource(
                video_path=video_path,
                title="",
                title_prefix="Dica",
                description="",
                tags="IA,automacao",
                category_id="28",
                privacy_status="private",
                made_for_kids=False,
                shorts_hashtag=True,
            )

        self.assertEqual(resource["snippet"]["title"], "Dica Automacao com IA para tarefas repetitivas")
        self.assertIn("#Shorts", resource["snippet"]["description"])
        self.assertEqual(resource["snippet"]["tags"], ["Shorts", "IA", "automacao"])
        self.assertEqual(resource["status"]["privacyStatus"], "private")
        self.assertFalse(resource["status"]["selfDeclaredMadeForKids"])


if __name__ == "__main__":
    unittest.main()
