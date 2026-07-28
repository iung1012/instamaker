import json
import unittest
from pathlib import Path
from unittest import mock

import video_qa


class PromptTest(unittest.TestCase):
    def test_cita_a_zona_segura_do_reels(self) -> None:
        prompt = video_qa.build_prompt("", "")
        self.assertIn(str(video_qa.SAFE_TOP), prompt)
        self.assertIn(str(video_qa.SAFE_BOTTOM), prompt)

    def test_inclui_o_hook_esperado_para_comparacao(self) -> None:
        # sem o texto esperado a IA nao tem como saber se faltou pedaco
        prompt = video_qa.build_prompt("IA que programa sozinha", "")
        self.assertIn("IA que programa sozinha", prompt)

    def test_sem_hook_nao_inventa_secao(self) -> None:
        self.assertNotIn("gancho deveria", video_qa.build_prompt("", ""))


class FormatReportTest(unittest.TestCase):
    def test_aprovado(self) -> None:
        texto = video_qa.format_report({"aprovado": True})
        self.assertIn("nenhum defeito", texto)

    def test_lista_os_dois_defeitos_e_os_detalhes(self) -> None:
        texto = video_qa.format_report({
            "aprovado": False,
            "cabeca_cortada": True,
            "texto_cortado": True,
            "problemas": ["frame 2: topo da cabeca fora do quadro"],
        })
        self.assertIn("Cabeca do personagem cortada", texto)
        self.assertIn("Texto cortado", texto)
        self.assertIn("frame 2", texto)


class ExtractFramesTest(unittest.TestCase):
    def test_extrai_frames_espalhados_de_um_video_real(self) -> None:
        video = Path("avatar_video.mp4")
        if not video.is_file():
            self.skipTest("avatar_video.mp4 ausente")

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            frames = video_qa.extract_frames(video, Path(tmp), count=4)
            self.assertEqual(len(frames), 4)
            for frame in frames:
                self.assertGreater(frame.stat().st_size, 0)

    def test_video_sem_duracao_da_erro_claro(self) -> None:
        with mock.patch.object(video_qa, "video_duration", return_value=0.0):
            with self.assertRaises(RuntimeError):
                video_qa.extract_frames(Path("x.mp4"), Path("."), count=1)


class ReviewFramesTest(unittest.TestCase):
    def test_sem_api_key_da_erro_e_nao_chama_a_rede(self) -> None:
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                video_qa.review_frames([Path("f.jpg")])

    def test_erro_do_sdk_vira_runtimeerror(self) -> None:
        # main() so trata RuntimeError; se o ClientError do SDK vazar, uma
        # instabilidade do Gemini derruba a publicacao inteira.
        class ClientError(Exception):
            pass

        frame = mock.Mock(spec=Path)
        frame.read_bytes.return_value = b"\xff\xd8"
        fake_client = mock.Mock()
        fake_client.models.generate_content.side_effect = ClientError("400 boom")
        fake_genai = mock.Mock()
        fake_genai.Client.return_value = fake_client
        modules = {
            "google": mock.Mock(genai=fake_genai),
            "google.genai": fake_genai,
            "google.genai.types": mock.Mock(),
        }
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=False), \
             mock.patch.dict("sys.modules", modules):
            with self.assertRaises(RuntimeError) as ctx:
                video_qa.review_frames([frame])
        self.assertIn("ClientError", str(ctx.exception))

    def test_manda_um_part_por_frame_e_devolve_o_json(self) -> None:
        frame = mock.Mock(spec=Path)
        frame.read_bytes.return_value = b"\xff\xd8jpeg"
        veredito = {"aprovado": False, "cabeca_cortada": True,
                    "texto_cortado": False, "problemas": ["cortou"]}

        fake_client = mock.Mock()
        fake_client.models.generate_content.return_value = mock.Mock(
            text=json.dumps(veredito)
        )
        fake_genai = mock.Mock()
        fake_genai.Client.return_value = fake_client

        modules = {
            "google": mock.Mock(genai=fake_genai),
            "google.genai": fake_genai,
            "google.genai.types": mock.Mock(),
        }
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=False), \
             mock.patch.dict("sys.modules", modules):
            result = video_qa.review_frames([frame, frame], hook="gancho")

        self.assertEqual(result, veredito)
        contents = fake_client.models.generate_content.call_args.kwargs["contents"]
        self.assertEqual(len(contents), 3, "prompt + 1 part por frame")
        self.assertIn("gancho", contents[0])


if __name__ == "__main__":
    unittest.main()
