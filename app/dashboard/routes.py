import os
from flask import render_template, request, redirect, url_for, session, flash, current_app, jsonify
from app.dashboard import dashboard_bp
from app.auth.routes import login_required
from app.services.analysis_service import process_competition_analysis, scan_competitors

ALLOWED_EXTENSIONS = {'json'}

def _allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@dashboard_bp.route('/')
@login_required
def index():
    """Redireciona a raiz para o dashboard."""
    return redirect(url_for('dashboard.dashboard'))


@dashboard_bp.route('/dashboard/clear')
@login_required
def clear():
    """Zera os resultados da sessão e redireciona para o dashboard limpo."""
    session.pop('analysis_results', None)
    return redirect(url_for('dashboard.dashboard'))


@dashboard_bp.route('/dashboard/scan', methods=['POST'])
@login_required
def scan():
    """
    Recebe um ou mais JSONs via AJAX e retorna a lista de concorrentes detectados
    automaticamente a partir da coluna 'observacao'.
    """
    uploaded_files = request.files.getlist('json_file')
    if not uploaded_files or all(f.filename == '' for f in uploaded_files):
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400

    try:
        # Lê bytes de todos os arquivos enviados
        all_bytes = [f.read() for f in uploaded_files if f.filename != '']
        competitors = scan_competitors(all_bytes)
        return jsonify({'competitors': competitors, 'file_count': len(all_bytes)})
    except ValueError as e:
        return jsonify({'error': str(e)}), 422
    except Exception as e:
        current_app.logger.error(f'Erro no scan: {e}')
        return jsonify({'error': 'Erro inesperado ao escanear o arquivo.'}), 500


@dashboard_bp.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    """
    GET:  Lê resultados da sessão (PRG) e exibe o painel.
    POST: Processa os JSONs (múltiplos), salva na sessão e redireciona (PRG).
    """
    from datetime import date as date_type
    user = session['user']

    # ── POST: processa e redireciona (PRG pattern) ──────────────
    if request.method == 'POST':
        competitor_name  = request.form.get('competitor_name', '').strip()
        uploaded_files   = request.files.getlist('json_file')

        # Leitura dos filtros de data (campos opcionais no form)
        date_from = None
        date_to   = None
        try:
            raw_from = request.form.get('date_from', '').strip()
            raw_to   = request.form.get('date_to',   '').strip()
            if raw_from:
                date_from = date_type.fromisoformat(raw_from)   # formato: YYYY-MM-DD
            if raw_to:
                date_to = date_type.fromisoformat(raw_to)
        except ValueError:
            flash('Formato de data inválido. Use o seletor de calendário.', 'warning')
            return redirect(url_for('dashboard.dashboard'))

        if not competitor_name:
            flash('Por favor, selecione o concorrente detectado.', 'warning')
            return redirect(url_for('dashboard.dashboard'))

        # Filtra arquivos válidos da lista
        valid_files   = [f for f in uploaded_files if f.filename != '' and _allowed_file(f.filename)]
        invalid_files = [f for f in uploaded_files if f.filename != '' and not _allowed_file(f.filename)]

        if invalid_files:
            flash(f'{len(invalid_files)} arquivo(s) ignorado(s): apenas .json é aceito.', 'warning')

        if not valid_files:
            flash('Por favor, selecione ao menos um arquivo JSON para análise.', 'warning')
            return redirect(url_for('dashboard.dashboard'))

        try:
            # Lê todos os arquivos como bytes e processa em conjunto
            all_bytes        = [f.read() for f in valid_files]
            analysis_results = process_competition_analysis(
                all_bytes, competitor_name,
                date_from=date_from,
                date_to=date_to,
            )

            # Informações de contexto para exibição no template
            analysis_results['files_count'] = len(all_bytes)
            analysis_results['date_from']   = str(date_from) if date_from else None
            analysis_results['date_to']     = str(date_to)   if date_to   else None

            session['analysis_results'] = analysis_results
            session.modified = True

        except ValueError as e:
            flash(f'Erro na análise: {e}', 'error')
        except Exception as e:
            current_app.logger.error(f'Erro inesperado no processamento: {e}')
            flash('Erro inesperado. Verifique o arquivo e tente novamente.', 'error')

        # Sempre redireciona para GET (PRG)
        return redirect(url_for('dashboard.dashboard'))


    # ── GET: lê resultados da sessão e os remove (one-shot) ─────
    analysis_results = session.pop('analysis_results', None)
    if analysis_results:
        session.modified = True

    return render_template(
        'dashboard/dashboard.html',
        user=user,
        results=analysis_results,
    )
