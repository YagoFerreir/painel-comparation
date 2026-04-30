import requests
from flask import current_app

def validate_bitrix24_user(email: str, password: str) -> dict | None:
    """
    Valida as credenciais do usuário na API do Bitrix24.

    Retorna um dict com dados do usuário se válido, ou None se inválido/erro.

    Args:
        email: E-mail do usuário.
        password: Senha do usuário.

    Returns:
        dict com 'id', 'name', 'email' do usuário, ou None em caso de falha.
    """
    webhook_url = current_app.config.get('BITRIX24_WEBHOOK_URL')

    if not webhook_url:
        current_app.logger.error("BITRIX24_WEBHOOK_URL não configurada.")
        return None

    # Endpoint do Bitrix24 para verificação de usuário por login
    # Documentação: https://training.bitrix24.com/rest_help/users/user_login.php
    endpoint = f"{webhook_url.rstrip('/')}/user.login/"

    payload = {
        'login': email,
        'password': password,
    }

    try:
        response = requests.post(endpoint, json=payload, timeout=10)
        response.raise_for_status()  # Lança exceção para status 4xx ou 5xx

        data = response.json()

        # A API retorna 'result' com os dados do usuário se bem-sucedido
        if data.get('result'):
            user_data = data['result']
            return {
                'id': user_data.get('ID'),
                'name': f"{user_data.get('NAME', '')} {user_data.get('LAST_NAME', '')}".strip(),
                'email': user_data.get('EMAIL', email),
            }

        return None  # Credenciais inválidas

    except requests.exceptions.Timeout:
        current_app.logger.error("Timeout ao conectar com o Bitrix24.")
        return None
    except requests.exceptions.ConnectionError:
        current_app.logger.error("Erro de conexão com o Bitrix24.")
        return None
    except requests.exceptions.HTTPError as e:
        current_app.logger.error(f"Erro HTTP do Bitrix24: {e}")
        return None
    except Exception as e:
        current_app.logger.error(f"Erro inesperado na autenticação: {e}")
        return None
