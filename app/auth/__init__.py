from flask import Blueprint

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# Importar as rotas depois de criar o Blueprint (evita circular imports)
from app.auth import routes  # noqa: E402, F401
