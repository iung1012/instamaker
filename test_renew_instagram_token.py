import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from renew_instagram_token import check_instagram_user, epoch_to_text, update_env_value


class RenewInstagramTokenHelpersTest(unittest.TestCase):
    def test_epoch_to_text_uses_utc(self) -> None:
        self.assertEqual(epoch_to_text(0), "1970-01-01T00:00:00+00:00")

    def test_update_env_value_replaces_only_requested_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "IG_USER_ID=123\nIG_ACCESS_TOKEN=old\nOTHER=value\n",
                encoding="utf-8",
            )

            update_env_value(env_path, "IG_ACCESS_TOKEN", "new-token")

            self.assertEqual(
                env_path.read_text(encoding="utf-8"),
                "IG_USER_ID=123\nIG_ACCESS_TOKEN=new-token\nOTHER=value\n",
            )

    def test_update_env_value_appends_missing_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("IG_USER_ID=123", encoding="utf-8")

            update_env_value(env_path, "IG_ACCESS_TOKEN", "new-token")

            self.assertEqual(
                env_path.read_text(encoding="utf-8"),
                "IG_USER_ID=123\nIG_ACCESS_TOKEN=new-token\n",
            )

    @patch("renew_instagram_token.graph_get")
    def test_check_instagram_user_uses_supported_fields(self, graph_get) -> None:
        graph_get.return_value = {"id": "123", "username": "example"}

        result = check_instagram_user("https://graph.facebook.com/v22.0", "token", "123")

        self.assertEqual(result["username"], "example")
        graph_get.assert_called_once_with(
            "https://graph.facebook.com/v22.0",
            "123",
            {"fields": "id,username", "access_token": "token"},
        )


if __name__ == "__main__":
    unittest.main()
