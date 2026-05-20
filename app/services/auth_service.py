from flask import current_app
from werkzeug.security import check_password_hash
from app.models import User

def validate_bitrix24_user(email: str, password: str) -> dict | None:
    """
    Substituído temporariamente por autenticação via PostgreSQL local.
    Busca o usuário pelo email e checa a hash da senha.
    
    Retorna um dict com dados do usuário se válido, ou None se inválido/erro.
    """
    try:
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password_hash, password):
            return user.to_dict()

        return None  # Credenciais inválidas

    except Exception as e:
        current_app.logger.error(f"Erro inesperado na autenticação (DB): {e}")
        return None
