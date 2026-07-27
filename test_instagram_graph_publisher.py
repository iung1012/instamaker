import unittest

from instagram_graph_publisher import extract_tmpfiles_download_url


class InstagramGraphPublisherHelpersTest(unittest.TestCase):
    def test_extract_tmpfiles_download_url_reads_real_download_link(self) -> None:
        html = """
        <div class="show-content">
            <p>
                <a class="download" href="https://tmpfiles.org/dl/1783545956.3cd9dec353ae1b48/wLwz3GbkeiF3/vivek4real__2074847469587009999_final.mp4">
                    Download (10.42 MB)
                </a>
            </p>
        </div>
        """

        self.assertEqual(
            extract_tmpfiles_download_url(html),
            "https://tmpfiles.org/dl/1783545956.3cd9dec353ae1b48/wLwz3GbkeiF3/vivek4real__2074847469587009999_final.mp4",
        )

    def test_extract_tmpfiles_download_url_fails_without_download_link(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Nao foi possivel extrair"):
            extract_tmpfiles_download_url("<html><body>Sem link</body></html>")


if __name__ == "__main__":
    unittest.main()
