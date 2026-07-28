"""Horarios de agendamento do bot, em horario de Brasilia.

A VPS roda em UTC. Calcular "18h" com datetime.now() local daria 15h no
feed, entao todo horario que o dono ve passa por aqui e nasce em
America/Sao_Paulo.

Os slots espelham o cron da pipeline (setup_vps_cron.sh): sao os horarios
que o projeto ja tratava como bons para publicar.
"""

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BR_TZ = ZoneInfo("America/Sao_Paulo")

# (hora, minuto) dos slots fixos oferecidos no chat.
SLOT_HOURS = [(9, 0), (12, 0), (15, 0), (18, 0), (21, 0)]

# Atalhos relativos, em minutos.
RELATIVE_SLOTS = [("daqui 1h", 60), ("daqui 3h", 180)]

WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]


def now_br(now: float | None = None) -> datetime:
    return datetime.fromtimestamp(now if now is not None else time.time(), BR_TZ)


def next_occurrence(hour: int, minute: int, now: float | None = None) -> float:
    """Proximo horario H:M em Brasilia. Se ja passou hoje, cai para amanha."""
    reference = now_br(now)
    candidate = reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= reference:
        candidate += timedelta(days=1)
    return candidate.timestamp()


def format_when(epoch: float, now: float | None = None) -> str:
    """Rotulo curto e sem ambiguidade: 'hoje 18:00', 'amanha 09:00', 'qui 12:00'."""
    alvo = now_br(epoch)
    hoje = now_br(now).date()
    delta = (alvo.date() - hoje).days
    relogio = alvo.strftime("%H:%M")
    if delta == 0:
        return f"hoje {relogio}"
    if delta == 1:
        return f"amanha {relogio}"
    return f"{WEEKDAYS[alvo.weekday()]} {alvo.strftime('%d/%m')} {relogio}"


def slot_options(now: float | None = None) -> list[tuple[str, float]]:
    """Opcoes oferecidas no chat, da mais proxima para a mais distante."""
    reference = now if now is not None else time.time()
    opcoes: list[tuple[str, float]] = [
        (rotulo, reference + minutos * 60) for rotulo, minutos in RELATIVE_SLOTS
    ]
    opcoes += [
        (format_when(epoch, now=reference), epoch)
        for epoch in (next_occurrence(h, m, now=reference) for h, m in SLOT_HOURS)
    ]
    # Sem duplicar horario quando um slot fixo cai quase junto de um relativo.
    vistos: set[int] = set()
    unicos: list[tuple[str, float]] = []
    for rotulo, epoch in sorted(opcoes, key=lambda item: item[1]):
        chave = int(epoch // 60)
        if chave in vistos:
            continue
        vistos.add(chave)
        unicos.append((rotulo, epoch))
    return unicos


def due_job_ids(jobs: dict, now: float | None = None) -> list[str]:
    """Ids agendados cuja hora chegou, do mais antigo para o mais novo."""
    reference = now if now is not None else time.time()
    vencidos = [
        (job_id, job["scheduled_for"])
        for job_id, job in jobs.items()
        if isinstance(job, dict) and job.get("scheduled_for")
        and float(job["scheduled_for"]) <= reference
    ]
    return [job_id for job_id, _ in sorted(vencidos, key=lambda item: item[1])]


def scheduled_summary(jobs: dict, now: float | None = None) -> str:
    agendados = [
        (job_id, job)
        for job_id, job in jobs.items()
        if isinstance(job, dict) and job.get("scheduled_for")
    ]
    if not agendados:
        return "Nenhum Reel agendado."
    agendados.sort(key=lambda item: item[1]["scheduled_for"])
    linhas = ["📅 Agendados:"]
    for job_id, job in agendados:
        quando = format_when(float(job["scheduled_for"]), now=now)
        gancho = (job.get("hook") or "sem gancho")[:40]
        linhas.append(f"• {quando} — {gancho}  [{job_id}]")
    return "\n".join(linhas)
