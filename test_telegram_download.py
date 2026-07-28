import unittest

from telegram_bot import YTDLP_NET_ARGS, download_failure_hint


class YtdlpNetArgsTest(unittest.TestCase):
    def test_sobe_o_socket_timeout_acima_do_padrao(self) -> None:
        # o padrao do yt-dlp e 20s, e o GraphQL do x.com estoura isso
        self.assertIn("--socket-timeout", YTDLP_NET_ARGS)
        valor = YTDLP_NET_ARGS[YTDLP_NET_ARGS.index("--socket-timeout") + 1]
        self.assertGreater(int(valor), 20)

    def test_tem_retentativas_no_extrator(self) -> None:
        # um timeout unico matava o trabalho inteiro
        self.assertIn("--extractor-retries", YTDLP_NET_ARGS)
        valor = YTDLP_NET_ARGS[YTDLP_NET_ARGS.index("--extractor-retries") + 1]
        self.assertGreaterEqual(int(valor), 3)


class DownloadFailureHintTest(unittest.TestCase):
    def test_timeout_nao_manda_procurar_cookies(self) -> None:
        erro = ("Unable to download JSON metadata: HTTPSConnectionPool("
                "host='x.com', port=443): Read timed out. (read timeout=20.0)")
        dica = download_failure_hint(erro)
        self.assertNotIn("cookies", dica.lower())
        self.assertIn("tempo esgotado", dica)

    def test_post_privado_manda_conferir_cookies(self) -> None:
        dica = download_failure_hint("ERROR: This tweet is protected / private")
        self.assertIn("cookies.json", dica)

    def test_erro_de_login_manda_conferir_cookies(self) -> None:
        dica = download_failure_hint("ERROR: NSFW tweet requires log in")
        self.assertIn("cookies.json", dica)

    def test_erro_desconhecido_sugere_tentar_de_novo(self) -> None:
        dica = download_failure_hint("ERROR: something entirely new")
        self.assertIn("de novo", dica)


if __name__ == "__main__":
    unittest.main()
