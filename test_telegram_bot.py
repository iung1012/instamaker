import unittest
from telegram_bot import job_keyboard, caption_keyboard, hook_keyboard


class TelegramBotKeyboardsTest(unittest.TestCase):
    def test_job_keyboard_contains_instagram_tiktok_and_ambos_buttons(self) -> None:
        kb = job_keyboard("job123", has_alternatives=False)
        self.assertIn("inline_keyboard", kb)

        rows = kb["inline_keyboard"]
        first_row = rows[0]
        callback_datas = [btn["callback_data"] for btn in first_row]

        self.assertIn("p_ig:job123", callback_datas)
        self.assertIn("p_tt:job123", callback_datas)
        self.assertIn("p_all:job123", callback_datas)

    def test_job_keyboard_with_alternatives(self) -> None:
        kb = job_keyboard("job123", has_alternatives=True)
        rows = kb["inline_keyboard"]
        self.assertEqual(rows[0][0]["callback_data"], "chg:job123")
        self.assertEqual(rows[1][0]["callback_data"], "p_ig:job123")


if __name__ == "__main__":
    unittest.main()
