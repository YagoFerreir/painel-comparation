import os
import logging
from dotenv import load_dotenv

load_dotenv()  # Carrega as variáveis do arquivo .env

logger = logging.getLogger(__name__)

# Valor padrão APENAS para desenvolvimento local — nunca usar em produção
_DEFAULT_SECRET = 'uma-chave-secreta-para-desenvolvimento'


class Config:
    """Configurações base compartilhadas por todos os ambientes."""

    SECRET_KEY = os.environ.get('SECRET_KEY', _DEFAULT_SECRET)
    MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # Limite de upload: 500MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')

    # Integração Bitrix24 (opcional)
    BITRIX24_WEBHOOK_URL = os.environ.get('BITRIX24_WEBHOOK_URL')

    # SQLAlchemy
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ── API do Cliente (credenciais NUNCA hardcoded) ──────────────────────────
    # Configure CLIENT_API_URL e CLIENT_API_TOKEN no .env (local)
    # ou nas variáveis de ambiente do painel de deploy (Render, Railway etc.)
    CLIENT_API_URL   = os.environ.get('CLIENT_API_URL')    # Ex: https://api.cliente.com/v1/produtos
    CLIENT_API_TOKEN = os.environ.get('CLIENT_API_TOKEN')  # Ex: Bearer token ou API key


class DevelopmentConfig(Config):
    """Configurações para desenvolvimento local."""
    DEBUG = True


class ProductionConfig(Config):
    """Configurações para produção (Render)."""
    DEBUG = False

    def __init__(self):
        # Avisa no log se a SECRET_KEY padrão estiver sendo usada em produção
        if os.environ.get('SECRET_KEY', _DEFAULT_SECRET) == _DEFAULT_SECRET:
            logger.critical(
                'SEGURANÇA: SECRET_KEY está com o valor padrão em produção! '
                'Defina a variável de ambiente SECRET_KEY antes do deploy.'
            )


# Mapa para seleção por string (usado em create_app)
config_map = {
    'development': DevelopmentConfig,
    'production':  ProductionConfig,
    'default':     DevelopmentConfig,
}
