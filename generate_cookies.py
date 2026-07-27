import asyncio
from twikit import Client
import getpass

async def generate():
    # Inicializa o cliente
    client = Client('en-US')
    
    print("--- Gerador de Cookies do Twitter (X) - Versão Corrigida ---")
    username = input("Usuário/Email: ")
    email = input("Email (opcional, pressione Enter se usou email acima): ")
    password = getpass.getpass("Senha: ")
    
    try:
        print("Tentando login (com métricas desativadas para evitar erro KEY_BYTE)...")
        # O parâmetro enable_ui_metrics=False resolve o erro "Couldn't get KEY_BYTE indices"
        # Esse erro ocorre quando a biblioteca tenta rodar um JavaScript do Twitter que falha no ambiente Python.
        await client.login(
            auth_info_1=username,
            auth_info_2=email if email else username,
            password=password,
            enable_ui_metrics=False
        )
        
        client.save_cookies('cookies.json')
        print("\nSucesso! Arquivo 'cookies.json' gerado.")
        
    except Exception as e:
        error_msg = str(e).lower()
        if "2fa" in error_msg or "two-factor" in error_msg or "code" in error_msg:
            print("\nAutenticação de 2 fatores detectada!")
            two_fa_code = input("Digite o código de verificação (SMS ou App): ")
            try:
                print("Tentando completar login com o código...")
                await client.login(
                    auth_info_1=username,
                    auth_info_2=email if email else username,
                    password=password,
                    totp_secret=two_fa_code,
                    enable_ui_metrics=False
                )
                client.save_cookies('cookies.json')
                print("\nSucesso! Arquivo 'cookies.json' gerado com 2FA.")
            except Exception as e2:
                print(f"\nErro ao completar 2FA: {e2}")
        else:
            print(f"\nErro ao fazer login: {e}")
            print("\nDica: Se o erro persistir, verifique se seu usuário e senha estão corretos.")

if __name__ == "__main__":
    asyncio.run(generate())