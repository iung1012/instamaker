import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import compose_test_video as ctv


class AvatarPanelFilterTest(unittest.TestCase):
    def test_ancora_vertical_no_topo_e_centraliza_horizontal(self) -> None:
        chain = ctv.build_avatar_panel("0:v", "out", 1080, 1000)[0]
        self.assertIn("(iw-1080)/2", chain, "horizontal continua centralizado")
        self.assertIn(f"(ih-1000)*{ctv.AVATAR_CROP_ANCHOR}", chain)
        self.assertNotIn("(ih-1000)/2", chain, "crop central cortava o rosto")

    def test_ancora_padrao_preserva_a_cabeca(self) -> None:
        # Se alguem subir esta constante, o topo do rosto volta a ser cortado.
        self.assertEqual(ctv.AVATAR_CROP_ANCHOR, 0.0)


class CtaOutOfVideoTest(unittest.TestCase):
    """O CTA vive na legenda do post, nao queimado no pixel."""

    def test_nao_existe_lista_de_ctas_no_compose(self) -> None:
        self.assertFalse(
            hasattr(ctv, "CTA_PHRASES"),
            "CTA_PHRASES voltaria a queimar a chamada no video",
        )

    def test_legenda_continua_garantindo_a_chamada(self) -> None:
        # Se isto quebrar, tiramos o CTA do video e ficamos sem nenhum.
        import caption_generator

        texto = caption_generator.ensure_comment_cta("Um post qualquer sem chamada.")
        self.assertTrue(caption_generator.has_comment_cta(texto))


class BuiltFilterTest(unittest.TestCase):
    """Olha o filtergraph que o ffmpeg vai receber de fato."""

    def _filtro(self, cta_text: str) -> str:
        return ctv.build_filter(
            hook_lines=["Gancho de teste"],
            hook_size=ctv.HOOK_SIZE,
            body_text="Corpo do texto",
            cta_text=cta_text,
            font_file="/tmp/fonte.ttf",
            text_dir=Path("/tmp"),
            total_duration=15.0,
            intro_seconds=1.4,
            outro_seconds=1.8,
            animate_top=False,
            text_box_opacity=0.0,
            src_w=1920,
            src_h=1080,
            top_h=760,
            band_h=150,
            pad_mode="blur",
            text_fade=0.2,
            body_y_override=0,
            band_color="black",
        )

    def test_sem_cta_o_filtro_nao_desenha_cta(self) -> None:
        chain = self._filtro("")
        self.assertNotIn("ctaline", chain)
        self.assertNotIn("ctabox", chain)

    def test_sem_cta_o_corpo_vai_ate_o_fim(self) -> None:
        # Sem isso, o trecho que o CTA ocupava ficaria sem texto nenhum.
        chain = self._filtro("")
        self.assertIn("bodyline", chain)
        self.assertIn("15.000", chain, "corpo deve durar ate o fim do video")

    def test_com_cta_explicito_volta_a_desenhar(self) -> None:
        chain = self._filtro("Comente aqui")
        self.assertIn("ctaline", chain)


class AvatarPanelPixelTest(unittest.TestCase):
    """Roda o filtro no ffmpeg de verdade e olha os pixels.

    Fonte com faixa vermelha no topo e azul no rodape: ancorado no topo, a
    saida mantem o vermelho (cabeca) e perde o azul (pes).
    """

    def setUp(self) -> None:
        self.ffmpeg = shutil.which("ffmpeg")
        if not self.ffmpeg:
            self.skipTest("ffmpeg nao encontrado no PATH")

    def _linha_topo(self, imagem: Path) -> tuple[int, int, int]:
        res = subprocess.run(
            [self.ffmpeg, "-v", "error", "-i", str(imagem),
             "-vf", "crop=1080:1:0:5,scale=1:1",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True,
        )
        return tuple(res.stdout[:3])

    def test_topo_da_fonte_sobrevive_ao_crop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "src.mp4"
            subprocess.run(
                [self.ffmpeg, "-y", "-loglevel", "error",
                 "-f", "lavfi", "-i", "color=c=green:s=1080x1920:d=1:r=30",
                 "-vf", "drawbox=x=0:y=0:w=1080:h=120:color=red@1:t=fill,"
                        "drawbox=x=0:y=1800:w=1080:h=120:color=blue@1:t=fill",
                 "-frames:v", "1", str(src)],
                check=True, capture_output=True,
            )

            chain = ctv.build_avatar_panel("0:v", "out", 1080, 1000)[0]
            out = tmpdir / "out.png"
            subprocess.run(
                [self.ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
                 "-filter_complex", chain, "-map", "[out]", "-frames:v", "1", str(out)],
                check=True, capture_output=True,
            )

            r, g, b = self._linha_topo(out)
            self.assertGreater(r, 150, f"topo deveria ser vermelho, veio rgb({r},{g},{b})")
            self.assertLess(g, 100)


if __name__ == "__main__":
    unittest.main()
