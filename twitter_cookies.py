import json
import os

def convert_cookies(input_file='cookies.json', output_file='cookies.json'):
    """
    Converte cookies exportados por extensões (como 'EditThisCookie' ou 'Cookie-Editor')
    para o formato interno do Twikit.
    """
    if not os.path.exists(input_file):
        print(f"Erro: O arquivo '{input_file}' não foi encontrado.")
        print("Certifique-se de exportar os cookies do seu navegador primeiro.")
        return

    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            raw_cookies = json.load(f)
        
        # O Twikit espera um dicionário simples de chave: valor
        # ou o formato específico que ele salva. 
        # Vamos tentar mapear os campos essenciais.
        
        formatted_cookies = {}
        for cookie in raw_cookies:
            name = cookie.get('name')
            value = cookie.get('value')
            if name and value:
                formatted_cookies[name] = value
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(formatted_cookies, f, indent=4)
            
        print(f"\nSucesso! Cookies convertidos e salvos em '{output_file}'.")
        print("Agora você pode rodar o 'twitter_timeline_scraper.py'.")
        
    except Exception as e:
        print(f"Erro ao converter cookies: {e}")

if __name__ == "__main__":
    print("--- Conversor de Cookies para Twitter Scraper ---")
    convert_cookies()