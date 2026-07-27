import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tiktok_sandbox_uploader import (
    build_authorize_url,
    build_code_challenge,
    generate_code_verifier,
    generate_state,
    is_tiktok_token_error,
    choose_privacy_level,
    save_env_values,
    token_expiry_utc,
)


class TikTokSandboxUploaderTest(unittest.TestCase):
    def test_build_code_challenge_is_url_safe(self) -> None:
        verifier = "abc123"
        challenge = build_code_challenge(verifier)
        self.assertEqual(len(challenge), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in challenge))

    def test_build_authorize_url_encodes_params(self) -> None:
        url = build_authorize_url(
            client_key="client_key_123",
            redirect_uri="http://127.0.0.1:8765/tiktok/callback/",
            scope="user.info.basic,video.upload",
            state="state-123",
            code_challenge="challenge-123",
        )

        parsed = urlparse(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "www.tiktok.com")
        self.assertEqual(parsed.path, "/v2/auth/authorize/")

        query = parse_qs(parsed.query)
        self.assertEqual(query["client_key"], ["client_key_123"])
        self.assertEqual(query["scope"], ["user.info.basic,video.upload"])
        self.assertEqual(query["redirect_uri"], ["http://127.0.0.1:8765/tiktok/callback/"])
        self.assertEqual(query["state"], ["state-123"])
        self.assertEqual(query["code_challenge"], ["challenge-123"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["response_type"], ["code"])

    def test_generated_values_are_not_empty(self) -> None:
        self.assertTrue(generate_state())
        verifier = generate_code_verifier()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertLessEqual(len(verifier), 128)
        self.assertTrue(all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~" for c in verifier))

    def test_save_env_values_updates_existing_keys(self) -> None:
        with TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("FOO=1\nBAR=2\n", encoding="utf-8")

            save_env_values(
                env_path,
                {
                    "BAR": "updated",
                    "BAZ": "3",
                },
            )

            self.assertEqual(
                env_path.read_text(encoding="utf-8"),
                "FOO=1\nBAR=updated\nBAZ=3\n",
            )

    def test_token_expiry_utc_returns_iso_string(self) -> None:
        expiry = token_expiry_utc(60)
        self.assertIn("T", expiry)
        self.assertTrue(expiry.endswith("+00:00") or expiry.endswith("Z"))

    def test_is_tiktok_token_error_matches_expected_messages(self) -> None:
        self.assertTrue(is_tiktok_token_error("TikTok error: access_token_invalid"))
        self.assertTrue(is_tiktok_token_error("invalid_grant"))
        self.assertFalse(is_tiktok_token_error("scope_not_authorized"))

    def test_choose_privacy_level_prefers_self_only(self) -> None:
        self.assertEqual(
            choose_privacy_level(["PUBLIC_TO_EVERYONE", "SELF_ONLY"]),
            "SELF_ONLY",
        )

    def test_choose_privacy_level_rejects_missing_requested_value(self) -> None:
        with self.assertRaises(ValueError):
            choose_privacy_level(["SELF_ONLY"], requested="PUBLIC_TO_EVERYONE")


if __name__ == "__main__":
    unittest.main()
