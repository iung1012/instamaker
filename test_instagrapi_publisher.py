import unittest
from pathlib import Path
from unittest import mock

import instagrapi_publisher as pub


class TrackStartSecondsTest(unittest.TestCase):
    def test_sem_highlight_comeca_do_zero(self) -> None:
        self.assertEqual(pub.track_start_seconds({}), 0.0)

    def test_usa_o_primeiro_highlight_em_segundos(self) -> None:
        self.assertEqual(
            pub.track_start_seconds({"highlight_start_times_in_ms": [12345]}), 12.345
        )

    def test_highlight_invalido_nao_quebra(self) -> None:
        self.assertEqual(
            pub.track_start_seconds({"highlight_start_times_in_ms": ["abc"]}), 0.0
        )


class DownloadTrackAudioTest(unittest.TestCase):
    def test_sem_uri_devolve_none(self) -> None:
        self.assertIsNone(pub.download_track_audio(None, {}, Path(".")))

    def test_usa_a_chave_uri_do_dict_cru(self) -> None:
        # pick_saved_track devolve o dict da API, nao o modelo Track: se alguem
        # trocar por track.uri isso volta a devolver None e o Reel sai mudo.
        client = mock.Mock()
        client.track_download_by_url.return_value = "/tmp/track.m4a"
        result = pub.download_track_audio(client, {"uri": "https://x/a.m4a"}, Path("/tmp"))
        self.assertEqual(result, Path("/tmp/track.m4a"))
        client.track_download_by_url.assert_called_once()


class PublishWithMusicTest(unittest.TestCase):
    """Regressao: o Instagram nao mixa a faixa do lado dele.

    clip_upload_with_music so manda metadado de atribuicao. Se o arquivo subir
    sem a musica dentro, o Reel sai mudo com o nome da musica em cima -- que
    era o bug. O upload precisa receber o arquivo ja mixado.
    """

    def setUp(self) -> None:
        self.session = mock.Mock(spec=Path)
        self.session.is_file.return_value = True
        self.video = Path("video_final.mp4")
        self.muxed = Path("video_final.music.mp4")
        self.audio = Path("track.m4a")
        self.track = {
            "title": "Faixa",
            "display_artist": "Artista",
            "uri": "https://x/a.m4a",
            "highlight_start_times_in_ms": [8000],
        }

    def _client(self):
        # pk/code viram JSON no history.json, entao precisam ser serializaveis
        client = mock.Mock()
        media = mock.Mock(pk="123", code="abc")
        client.clip_upload_with_music.return_value = media
        client.clip_upload.return_value = media
        return client

    def _publish(self, client):
        with mock.patch.object(pub, "build_client", return_value=client), \
             mock.patch.object(pub, "make_thumbnail", return_value=mock.Mock(spec=Path)), \
             mock.patch.object(pub, "pick_saved_track", return_value=self.track), \
             mock.patch.object(pub, "download_track_audio", return_value=mock.Mock(spec=Path)), \
             mock.patch.object(pub, "mux_track", return_value=mock.Mock(spec=Path)) as mux, \
             mock.patch.object(pub.Path, "write_text"), \
             mock.patch.object(pub.Path, "is_file", return_value=False):
            rc = pub.do_publish(self.session, self.video, "legenda", music=True)
        return rc, mux

    def test_sobe_o_arquivo_mixado_e_nao_o_original(self) -> None:
        client = self._client()
        rc, mux = self._publish(client)

        self.assertEqual(rc, 0)
        mux.assert_called_once()
        self.assertEqual(mux.call_args.args[2], 8.0, "deve cortar no highlight da faixa")

        client.clip_upload_with_music.assert_called_once()
        enviado = client.clip_upload_with_music.call_args.args[0]
        self.assertIs(enviado, mux.return_value)
        self.assertIsNot(enviado, self.video)
        client.clip_upload.assert_not_called()

    def test_declara_audio_original_zerado(self) -> None:
        # o arquivo ja sobe so com a musica; dizer original_volume=1.0 era o
        # que contradizia o proprio ffmpeg no codigo antigo
        client = self._client()
        self._publish(client)
        kwargs = client.clip_upload_with_music.call_args.kwargs
        self.assertEqual(kwargs["original_volume"], 0.0)
        self.assertEqual(kwargs["music_volume"], 1.0)
        self.assertEqual(kwargs["audio_asset_start_time"], 8000)

    def test_sem_audio_baixavel_publica_com_som_original(self) -> None:
        client = self._client()
        with mock.patch.object(pub, "build_client", return_value=client), \
             mock.patch.object(pub, "make_thumbnail", return_value=mock.Mock(spec=Path)), \
             mock.patch.object(pub, "pick_saved_track", return_value=self.track), \
             mock.patch.object(pub, "download_track_audio", return_value=None), \
             mock.patch.object(pub, "mux_track") as mux, \
             mock.patch.object(pub.Path, "write_text"), \
             mock.patch.object(pub.Path, "is_file", return_value=False):
            rc = pub.do_publish(self.session, self.video, "legenda", music=True)

        self.assertEqual(rc, 0)
        mux.assert_not_called()
        client.clip_upload_with_music.assert_not_called()
        client.clip_upload.assert_called_once()


class SimulateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session = mock.Mock(spec=Path)
        self.session.is_file.return_value = True
        self.video = Path("video_final.mp4")
        self.track = {"title": "Faixa", "display_artist": "Artista",
                      "uri": "https://x/a.m4a", "highlight_start_times_in_ms": [8000]}

    def test_monta_o_arquivo_mas_nao_publica(self) -> None:
        client = mock.Mock()
        audio = mock.Mock(spec=Path)
        with mock.patch.object(pub, "build_client", return_value=client), \
             mock.patch.object(pub, "pick_saved_track", return_value=self.track), \
             mock.patch.object(pub, "download_track_audio", return_value=audio), \
             mock.patch.object(pub, "mux_track") as mux, \
             mock.patch.object(pub, "measure_volume", return_value="-8.0 dB"):
            rc = pub.do_simulate(self.session, self.video, music=True, music_name="")

        self.assertEqual(rc, 0)
        mux.assert_called_once()
        client.clip_upload.assert_not_called()
        client.clip_upload_with_music.assert_not_called()
        audio.unlink.assert_called_once()

    def test_sem_sessao_falha_sem_chamar_o_instagram(self) -> None:
        session = mock.Mock(spec=Path)
        session.is_file.return_value = False
        with mock.patch.object(pub, "build_client") as build:
            rc = pub.do_simulate(session, self.video, music=True, music_name="")
        self.assertEqual(rc, 1)
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
