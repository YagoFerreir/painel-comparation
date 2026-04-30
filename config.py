import os
from dotenv import load_dotenv

load_dotenv()  # Carrega as variáveis do arquivo .env

class Config:
    """Configurações base compartilhadas por todos os ambientes."""
    SECRET_KEY = os.environ.get('SECRET_KEY', 'uma-chave-secreta-para-desenvolvimento')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # Limite de upload: 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    BITRIX24_WEBHOOK_URL = os.environ.get('BITRIX24_WEBHOOK_URL')

class DevelopmentConfig(Config):
    """Configurações para desenvolvimento local."""
    DEBUG = True

class ProductionConfig(Config):
    """Configurações para produção (Render)."""
    DEBUG = False

# Mapa para seleção por string (usado em create_app)
config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
