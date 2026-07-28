import unittest
from datetime import datetime, timezone

import schedule_slots as ss


def epoch_br(ano, mes, dia, hora, minuto=0) -> float:
    return datetime(ano, mes, dia, hora, minuto, tzinfo=ss.BR_TZ).timestamp()


class NextOccurrenceTest(unittest.TestCase):
    def test_hoje_quando_ainda_nao_passou(self) -> None:
        agora = epoch_br(2026, 7, 28, 10, 0)
        alvo = ss.next_occurrence(18, 0, now=agora)
        self.assertEqual(ss.now_br(alvo).hour, 18)
        self.assertEqual(ss.now_br(alvo).day, 28)

    def test_amanha_quando_ja_passou(self) -> None:
        agora = epoch_br(2026, 7, 28, 19, 0)
        alvo = ss.next_occurrence(18, 0, now=agora)
        self.assertEqual(ss.now_br(alvo).day, 29)

    def test_calcula_em_brasilia_e_nao_em_utc(self) -> None:
        # A VPS roda em UTC: 18:00 BRT sao 21:00 UTC. Se isto quebrar, o post
        # sai tres horas fora do horario que o dono escolheu.
        agora = epoch_br(2026, 7, 28, 10, 0)
        alvo = ss.next_occurrence(18, 0, now=agora)
        self.assertEqual(datetime.fromtimestamp(alvo, timezone.utc).hour, 21)


class FormatWhenTest(unittest.TestCase):
    def test_hoje(self) -> None:
        agora = epoch_br(2026, 7, 28, 10, 0)
        self.assertEqual(ss.format_when(epoch_br(2026, 7, 28, 18), now=agora), "hoje 18:00")

    def test_amanha(self) -> None:
        agora = epoch_br(2026, 7, 28, 10, 0)
        self.assertEqual(ss.format_when(epoch_br(2026, 7, 29, 9), now=agora), "amanha 09:00")

    def test_dia_distante_mostra_a_data(self) -> None:
        agora = epoch_br(2026, 7, 28, 10, 0)
        rotulo = ss.format_when(epoch_br(2026, 7, 31, 12), now=agora)
        self.assertIn("31/07", rotulo)


class SlotOptionsTest(unittest.TestCase):
    def test_vem_em_ordem_cronologica(self) -> None:
        agora = epoch_br(2026, 7, 28, 10, 0)
        epochs = [e for _, e in ss.slot_options(now=agora)]
        self.assertEqual(epochs, sorted(epochs))

    def test_nunca_oferece_horario_no_passado(self) -> None:
        agora = epoch_br(2026, 7, 28, 22, 30)
        for rotulo, epoch in ss.slot_options(now=agora):
            self.assertGreater(epoch, agora, f"slot {rotulo} ja passou")

    def test_nao_repete_o_mesmo_minuto(self) -> None:
        agora = epoch_br(2026, 7, 28, 11, 0)  # "daqui 1h" bate com o slot 12:00
        minutos = [int(e // 60) for _, e in ss.slot_options(now=agora)]
        self.assertEqual(len(minutos), len(set(minutos)))


class DueJobsTest(unittest.TestCase):
    def test_pega_so_os_vencidos_e_em_ordem(self) -> None:
        agora = epoch_br(2026, 7, 28, 12, 0)
        jobs = {
            "novo": {"scheduled_for": agora + 3600},
            "velho": {"scheduled_for": agora - 7200},
            "recente": {"scheduled_for": agora - 60},
            "sem_hora": {"caption": "x"},
        }
        self.assertEqual(ss.due_job_ids(jobs, now=agora), ["velho", "recente"])

    def test_job_sem_agendamento_nunca_vence(self) -> None:
        self.assertEqual(ss.due_job_ids({"a": {"video": "x.mp4"}}), [])

    def test_ignora_entrada_corrompida(self) -> None:
        self.assertEqual(ss.due_job_ids({"a": "nao e dict"}), [])


class SummaryTest(unittest.TestCase):
    def test_sem_agendados(self) -> None:
        self.assertIn("Nenhum", ss.scheduled_summary({}))

    def test_lista_ordenada_com_gancho(self) -> None:
        agora = epoch_br(2026, 7, 28, 10, 0)
        jobs = {
            "b": {"scheduled_for": epoch_br(2026, 7, 28, 21), "hook": "Gancho B"},
            "a": {"scheduled_for": epoch_br(2026, 7, 28, 12), "hook": "Gancho A"},
        }
        texto = ss.scheduled_summary(jobs, now=agora)
        self.assertLess(texto.index("Gancho A"), texto.index("Gancho B"))
        self.assertIn("hoje 12:00", texto)


if __name__ == "__main__":
    unittest.main()
