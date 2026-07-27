import json
import os
import sys
import time
from pathlib import Path

def get_recent_stats(session_path: str = ".ig_session.json", history_path: str = "history.json"):
    history_file = Path(history_path)
    if not history_file.is_file():
        return "Nenhum histórico de posts encontrado."
    
    try:
        history = json.loads(history_file.read_text())
    except Exception:
        return "Erro ao ler history.json."
    
    if not history:
        return "Histórico de posts vazio."

    # Filtrar posts das ultimas 48 horas
    now = time.time()
    recent_posts = [p for p in history if (now - p.get("timestamp", 0)) < 48 * 3600]
    
    if not recent_posts:
        return "Nenhum post publicado nas últimas 48 horas."

    from instagrapi import Client
    client = Client()
    client.delay_range = [1, 3]
    if Path(session_path).is_file():
        client.load_settings(session_path)
    else:
        return "Sessão do Instagram não encontrada. Faça login primeiro."
    
    lines = ["📊 *Relatório de Visualizações (Últimas 48h)*\n"]
    
    for post in reversed(recent_posts):
        try:
            # client.media_info pega os dados mais recentes do post
            media = client.media_info(post["pk"])
            url = f"https://www.instagram.com/reel/{media.code}/"
            
            hours_ago = (now - post.get("timestamp", now)) / 3600
            time_str = f"{int(hours_ago)}h atrás" if hours_ago >= 1 else "Agora"
            
            lines.append(f"• [Reel {time_str}]({url})")
            lines.append(f"  👁️ Views: {media.view_count}")
            lines.append(f"  ❤️ Likes: {media.like_count}")
            lines.append(f"  💬 Comentários: {media.comment_count}\n")
            
        except Exception as e:
            lines.append(f"• [Reel {post.get('code')}] Erro ao buscar stats: {e}\n")
    
    return "\n".join(lines)

if __name__ == "__main__":
    print(get_recent_stats())
