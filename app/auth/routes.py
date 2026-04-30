from functools import wraps
from flask import render_template, request, redirect, url_for, session, flash
from app.auth import auth_bp
from app.services.auth_service import validate_bitrix24_user

def login_required(f):
    """
    Decorator que protege rotas que exigem autenticação.
    Uso: @login_required acima de qualquer rota que deva ser protegida.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            flash('Faça login para acessar esta página.', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """
    GET: Exibe o formulário de login.
    POST: Processa as credenciais e redireciona.
    """
    # Se o usuário já está logado, manda direto para o dashboard
    if 'user' in session:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if not email or not password:
            flash('Por favor, preencha e-mail e senha.', 'warning')
            return render_template('auth/login.html')

        user = validate_bitrix24_user(email, password)

        if user:
            # Salva os dados do usuário na sessão Flask (cookie assinado)
            session['user'] = user
            session.permanent = True  # Mantém a sessão entre fechamentos do browser
            flash(f"Bem-vindo, {user['name']}!", 'success')
            return redirect(url_for('dashboard.index'))
        else:
            flash('Credenciais inválidas ou serviço indisponível. Tente novamente.', 'error')

    return render_template('auth/login.html')

@auth_bp.route('/logout')
def logout():
    """Remove a sessão do usuário."""
    session.pop('user', None)
    flash('Você saiu do sistema.', 'info')
    return redirect(url_for('auth.login'))
