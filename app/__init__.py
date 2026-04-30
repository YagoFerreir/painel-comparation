import os
from flask import Flask
from config import config_map

def create_app(config_name=None):
    """
    Application Factory: cria e configura a instância do Flask.
    Registra todos os Blueprints aqui.
    """
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'default')

    app = Flask(__name__)
    app.config.from_object(config_map[config_name])

    # Garante que a pasta de uploads existe
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # --- Registro de Blueprints ---
    # Importamos DENTRO da função para evitar importações circulares
    from app.auth import auth_bp
    from app.dashboard import dashboard_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)

    return app
