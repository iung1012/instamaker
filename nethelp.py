"""Ajuste de rede para ambientes com IPv6 anunciado mas sem rota.

Sintoma: o host resolve para IPv4 e IPv6, o `urllib` tenta o IPv6 primeiro e
fica preso ate o timeout do TCP (~20s no Windows, ate 2min em alguns Linux)
antes de cair para o IPv4. O `curl` nao sofre porque implementa Happy
Eyeballs, testando as duas familias em paralelo.

Medido em api.telegram.org nesta maquina: 21,9s por chamada com a ordem
padrao, 1,0s com IPv4 na frente.

A correcao apenas reordena o resultado do getaddrinfo, colocando IPv4
primeiro. O IPv6 continua na lista como alternativa, entao um host sem IPv4
segue funcionando: o socket so cai para a segunda familia se a primeira
falhar, e em rede sem IPv4 a falha e imediata (ENETUNREACH), nao um timeout.

Desligue com PREFER_IPV4=0 no .env.
"""

import os
import socket

_original_getaddrinfo = None


def _ipv4_first(host, port, family=0, type=0, proto=0, flags=0):
    results = _original_getaddrinfo(host, port, family, type, proto, flags)
    return sorted(results, key=lambda item: 0 if item[0] == socket.AF_INET else 1)


def prefer_ipv4(enabled: bool | None = None) -> bool:
    """Instala a preferencia por IPv4. Devolve se ficou ativa."""
    global _original_getaddrinfo

    if enabled is None:
        enabled = os.getenv("PREFER_IPV4", "1").strip().lower() not in {"0", "false", "no", "off"}

    if not enabled:
        return False
    if _original_getaddrinfo is not None:
        return True

    _original_getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = _ipv4_first
    return True


def restore() -> None:
    global _original_getaddrinfo
    if _original_getaddrinfo is not None:
        socket.getaddrinfo = _original_getaddrinfo
        _original_getaddrinfo = None
