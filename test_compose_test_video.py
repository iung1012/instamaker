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
