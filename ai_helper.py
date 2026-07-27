import os
import json
from google import genai
from google.genai import types

def is_ai_available() -> bool:
    """Retorna True se a chave da API do Gemini estiver configurada."""
    return bool(os.getenv("GEMINI_API_KEY"))

def generate_hooks_and_caption(tweet_text: str) -> dict:
    """
    Usa o Gemini para gerar 3 opções de ganchos virais e 1 legenda para um vídeo.
    Retorna um dicionário JSON no formato:
    {
      "opcoes_gancho": ["gancho 1", "gancho 2", "gancho 3"],
      "legenda": "texto da legenda com hashtags"
    }
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY nao encontrada no .env")

    client = genai.Client(api_key=api_key)
    
    prompt = (
        f"Baseado no texto original deste vídeo ou tweet: '{tweet_text}'\n\n"
        "Crie 3 opções de gancho (hook) curtos, impactantes e virais (no máximo 5 a 6 palavras cada) "
        "para colocar no topo de um vídeo vertical (Reels/TikTok).\n"
        "Além disso, crie uma única legenda engajadora com no máximo 2 parágrafos, incluindo hashtags relevantes.\n\n"
        "Retorne APENAS um objeto JSON no formato exato abaixo, sem blocos de código Markdown ao redor:\n"
        "{\n"
        '  "opcoes_gancho": ["opcao 1", "opcao 2", "opcao 3"],\n'
        '  "legenda": "texto da legenda aqui"\n'
        "}"
    )

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.7,
            response_mime_type="application/json",
            response_schema={
                "type": "OBJECT",
                "properties": {
                    "opcoes_gancho": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "3 opções curtas de ganchos impactantes."
                    },
                    "legenda": {
                        "type": "STRING",
                        "description": "A legenda engajadora para o vídeo, com hashtags."
                    }
                },
                "required": ["opcoes_gancho", "legenda"]
            }
        ),
    )

    try:
        # A resposta pode conter blocos markdown dependendo da versão da SDK/comportamento da API.
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:-3].strip()
        elif text.startswith("```"):
            text = text[3:-3].strip()
        return json.loads(text)
    except Exception as e:
        raise RuntimeError(f"Erro ao converter resposta da IA em JSON: {e}")
