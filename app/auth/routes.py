from functools import wraps
from flask import render_template, request, redirect, url_for, session, flash
from app.auth import auth_bp
from app.services.auth_service import validate_bitrix24_user


def login_required(f):
    """Decorator que protege rotas que exigem autenticação."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            flash('Faça login para acessar esta página.', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


# ── Login ────────────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'user' in session:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if not email or not password:
            flash('Por favor, preencha login e senha.', 'warning')
            return render_template('auth/login.html', active_tab='login')

        user = validate_bitrix24_user(email, password)

        if user:
            session['user'] = user
            session.permanent = True
            flash(f"Bem-vindo, {user['name']}!", 'success')
            return redirect(url_for('dashboard.index'))
        else:
            flash('Credenciais inválidas. Verifique seu login e senha.', 'error')

    return render_template('auth/login.html', active_tab='login')


# ── Cadastro ─────────────────────────────────────────────────────────────────

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """
    Cadastro de novos usuários — solução temporária enquanto o Bitrix24
    não está integrado. Qualquer pessoa com o link pode se cadastrar.
    """
    if 'user' in session:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        from app.models import db, User
        from werkzeug.security import generate_password_hash

        name      = request.form.get('name', '').strip()
        email     = request.form.get('email', '').strip()
        password  = request.form.get('password', '')
        password2 = request.form.get('password2', '')

        # Validações
        errors = []
        if not name:
            errors.append('O nome é obrigatório.')
        if not email:
            errors.append('O login é obrigatório.')
        if len(password) < 6:
            errors.append('A senha deve ter pelo menos 6 caracteres.')
        if password != password2:
            errors.append('As senhas não conferem.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('auth/login.html', active_tab='register',
                                   form_name=name, form_email=email)

        # Verifica se login já existe
        existing = User.query.filter_by(email=email).first()
        if existing:
            flash('Este login já está em uso. Escolha outro.', 'error')
            return render_template('auth/login.html', active_tab='register',
                                   form_name=name, form_email=email)

        # Cria o usuário
        new_user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(password)
        )
        db.session.add(new_user)
        db.session.commit()

        flash(f'Conta criada com sucesso! Bem-vindo, {name}. Faça seu login.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/login.html', active_tab='register')


# ── Logout ────────────────────────────────────────────────────────────────────

@auth_bp.route('/logout')
def logout():
    session.pop('user', None)
    flash('Você saiu do sistema.', 'info')
    return redirect(url_for('auth.login'))


