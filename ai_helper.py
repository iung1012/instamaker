"""Ganchos e legenda via LLM.

Migrado do Gemini para o cliente unico em `llm_client` (API compativel com OpenAI,
hoje apontando para a Standard Compute). A funcao publica e a assinatura continuam
identicas, entao nada mais no projeto precisou mudar.
"""

import llm_client

SYSTEM = (
    "Voce escreve conteudo curto para Reels e TikTok em PORTUGUES DO BRASIL. "
    "Tom direto e tecnico, sem 'revolucionario', sem 'game changer', "
    "sem clickbait mentiroso."
)


def is_ai_available() -> bool:
    """True se houver chave de LLM configurada."""
    return llm_client.is_available()


def generate_hooks_and_caption(tweet_text: str) -> dict:
    """
    Gera 3 ganchos virais e 1 legenda para um video.

    Retorna:
    {
      "opcoes_gancho": ["gancho 1", "gancho 2", "gancho 3"],
      "legenda": "texto da legenda com hashtags"
    }
    """
    prompt = (
        f"Texto original do video/tweet:\n{tweet_text}\n\n"
        "1) Crie 3 ganchos curtos e impactantes (5 a 6 palavras cada) para o topo "
        "de um video vertical.\n\n"
        "2) Crie a legenda do Instagram seguindo EXATAMENTE esta estrutura, com "
        "linhas em branco de verdade separando cada bloco:\n"
        "   linha 1: frase de impacto, sozinha\n"
        "   (linha em branco)\n"
        "   2 a 3 frases curtas explicando, cada uma em sua propria linha\n"
        "   (linha em branco)\n"
        "   uma pergunta ou chamada curta para o leitor\n"
        "   (linha em branco)\n"
        "   as hashtags, todas juntas na ultima linha\n\n"
        "Nunca entregue a legenda como um paragrafo unico e corrido.\n\n"
        'Formato de saida: {"opcoes_gancho": ["...", "...", "..."], "legenda": "..."}'
    )
    data = llm_client.chat_json(prompt, system=SYSTEM)
    hooks = [str(h).strip() for h in (data.get("opcoes_gancho") or []) if str(h).strip()]
    return {
        "opcoes_gancho": hooks[:3],
        "legenda": str(data.get("legenda") or "").strip(),
    }
