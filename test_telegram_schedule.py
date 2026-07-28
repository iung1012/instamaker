"""Integracao do agendamento com o Bot: o clique marca, o loop publica."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import schedule_slots
import telegram_bot as tb


class ScheduleIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        self.bot = tb.Bot(
            token="t", project_dir=self.project, admins={1}, python_bin="python",
        )
        self.bot.jobs = {"j1": {"chat_id": 55, "video": "v.mp4",
                                "caption": "c", "hook": "Gancho"}}
        self.ditos: list[str] = []
        self.bot.say = lambda chat_id, text, markup=None: self.ditos.append(text)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _clicar(self, action: str, partes: list[str]) -> None:
        with mock.patch.object(tb, "answer_callback"):
            self.bot.handle_schedule(55, "cb", action, partes, self.bot.jobs["j1"])

    def test_sca_grava_o_horario_e_persiste(self) -> None:
        quando = time.time() + 3600
        self._clicar("sca", ["j1", str(int(quando))])

        self.assertAlmostEqual(self.bot.jobs["j1"]["scheduled_for"], int(quando), places=0)
        # tem de sobreviver a um restart do bot
        self.assertIn("scheduled_for", tb.load_jobs(self.project)["j1"])

    def test_scx_cancela(self) -> None:
        self.bot.jobs["j1"]["scheduled_for"] = time.time() + 3600
        self._clicar("scx", ["j1"])
        self.assertNotIn("scheduled_for", self.bot.jobs["j1"])

    def test_horario_invalido_nao_agenda(self) -> None:
        self._clicar("sca", ["j1", "nao-e-numero"])
        self.assertNotIn("scheduled_for", self.bot.jobs["j1"])

    def test_loop_publica_o_vencido_e_limpa_o_job(self) -> None:
        self.bot.jobs["j1"]["scheduled_for"] = time.time() - 10
        publicados = []
        self.bot.publish = lambda chat_id, job, destination="all": publicados.append(
            (chat_id, destination)
        )

        # uma volta so do loop, sem dormir
        with mock.patch.object(tb.time, "sleep", side_effect=StopIteration):
            with self.assertRaises(StopIteration):
                self.bot.schedule_loop()

        self.assertEqual(publicados, [(55, "all")])
        self.assertNotIn("j1", self.bot.jobs, "job publicado deve sair da lista")

    def test_loop_ignora_o_que_ainda_nao_venceu(self) -> None:
        self.bot.jobs["j1"]["scheduled_for"] = time.time() + 3600
        publicados = []
        self.bot.publish = lambda *a, **k: publicados.append(a)

        with mock.patch.object(tb.time, "sleep", side_effect=StopIteration):
            with self.assertRaises(StopIteration):
                self.bot.schedule_loop()

        self.assertEqual(publicados, [])
        self.assertIn("j1", self.bot.jobs)

    def test_falha_ao_publicar_nao_derruba_o_agendador(self) -> None:
        self.bot.jobs["j1"]["scheduled_for"] = time.time() - 10

        def explode(*a, **k):
            raise RuntimeError("instagram fora do ar")

        self.bot.publish = explode
        with mock.patch.object(tb.time, "sleep", side_effect=StopIteration):
            with self.assertRaises(StopIteration):
                self.bot.schedule_loop()  # nao pode propagar o RuntimeError

    def test_arquivo_do_agendado_fica_protegido_da_faxina(self) -> None:
        # a faxina apaga renders velhos; um Reel com hora marcada nao pode sumir
        self.bot.jobs["j1"]["scheduled_for"] = time.time() + 86400
        self.assertIn("v.mp4", " ".join(self.bot.protected_paths()))


class ScheduleKeyboardTest(unittest.TestCase):
    def test_botao_agendar_no_teclado_do_job(self) -> None:
        datas = [b["callback_data"] for row in tb.job_keyboard("j1", False)["inline_keyboard"]
                 for b in row]
        self.assertIn("sch:j1", datas)

    def test_slots_carregam_epoch_no_callback(self) -> None:
        agora = time.time()
        kb = tb.schedule_keyboard("j1", now=agora)
        datas = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("scb:j1", datas)
        slots = [d for d in datas if d.startswith("sca:")]
        self.assertTrue(slots)
        for slot in slots:
            _, job_id, epoch = slot.split(":")
            self.assertEqual(job_id, "j1")
            self.assertGreater(float(epoch), agora)

    def test_callback_cabe_no_limite_do_telegram(self) -> None:
        # o Telegram corta callback_data acima de 64 bytes
        kb = tb.schedule_keyboard("a" * 10, now=time.time())
        for row in kb["inline_keyboard"]:
            for botao in row:
                self.assertLessEqual(len(botao["callback_data"].encode()), 64)


if __name__ == "__main__":
    unittest.main()
