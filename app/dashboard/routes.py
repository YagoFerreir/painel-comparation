import os
from flask import render_template, request, redirect, url_for, session, flash, current_app
from werkzeug.utils import secure_filename
from app.dashboard import dashboard_bp
from app.auth.routes import login_required
from app.services.analysis_service import process_competition_analysis

ALLOWED_EXTENSIONS = {'json'}

def _allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@dashboard_bp.route('/')
@login_required
def index():
    """Redireciona a raiz para o dashboard."""
    return redirect(url_for('dashboard.dashboard'))

@dashboard_bp.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    """
    GET: Renderiza o painel vazio, pronto para receber o upload.
    POST: Processa o JSON enviado e retorna as métricas para o template.
    """
    user = session['user']
    analysis_results = None
    error_message = None

    if request.method == 'POST':
        competitor_name = request.form.get('competitor_name', '').strip()
        uploaded_file = request.files.get('json_file')

        # Validações do formulário
        if not competitor_name:
            flash('Por favor, informe o nome do concorrente.', 'warning')
            return render_template('dashboard/dashboard.html', user=user)

        if not uploaded_file or uploaded_file.filename == '':
            flash('Por favor, selecione um arquivo JSON para análise.', 'warning')
            return render_template('dashboard/dashboard.html', user=user)

        if not _allowed_file(uploaded_file.filename):
            flash('Formato inválido. Apenas arquivos .json são aceitos.', 'error')
            return render_template('dashboard/dashboard.html', user=user)

        try:
            # Lê os bytes do arquivo diretamente (sem salvar em disco)
            json_bytes = uploaded_file.read()

            analysis_results = process_competition_analysis(json_bytes, competitor_name)

        except ValueError as e:
            # Erros de negócio (JSON inválido, coluna ausente, concorrente não encontrado)
            error_message = str(e)
            flash(f'Erro na análise: {error_message}', 'error')
        except Exception as e:
            current_app.logger.error(f"Erro inesperado no processamento: {e}")
            error_message = "Ocorreu um erro inesperado. Verifique o arquivo e tente novamente."
            flash(error_message, 'error')

    return render_template(
        'dashboard/dashboard.html',
        user=user,
        results=analysis_results,
        error=error_message,
    )
