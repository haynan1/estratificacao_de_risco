"""Camada de segurança: sessão, papéis, CSRF, chave secreta, throttle e admin.

Autenticação por sessão e CSRF são implementados sem dependências externas,
mantendo a promessa de funcionar offline, em rede local fechada, após instalado.
"""

import hmac
import os
import secrets
import time
from functools import wraps
from pathlib import Path

from flask import abort, flash, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from models import Usuario, db


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_ENDPOINTS = {"login", "static"}
SENHA_MIN = 8

# Throttle de login em memória (suficiente para uso em rede local de uma unidade).
_FAILED_LOGINS = {}
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300


# ----------------------------------------------------------------------------
# Chave secreta e CSRF
# ----------------------------------------------------------------------------
def resolve_secret_key(instance_path):
    """Nunca usa um default previsível. Ordem: env > arquivo persistido > gerado."""
    env_key = os.getenv("SECRET_KEY")
    if env_key and env_key.strip():
        return env_key.strip()
    key_file = Path(instance_path) / "secret_key"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        stored = key_file.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    generated = secrets.token_hex(32)
    key_file.write_text(generated, encoding="utf-8")
    return generated


def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_hex(32)
        session["csrf_token"] = token
    return token


def csrf_is_valid():
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken", "")
    stored = session.get("csrf_token", "")
    return bool(stored) and hmac.compare_digest(str(sent), str(stored))


# ----------------------------------------------------------------------------
# Senhas
# ----------------------------------------------------------------------------
def hash_senha(senha):
    return generate_password_hash(senha)


def senha_confere(usuario, senha):
    return bool(usuario) and check_password_hash(usuario.password_hash, senha)


def senha_valida(senha):
    return isinstance(senha, str) and len(senha) >= SENHA_MIN


# ----------------------------------------------------------------------------
# Throttle de login
# ----------------------------------------------------------------------------
def _cleanup_attempts(key, now):
    recent = [t for t in _FAILED_LOGINS.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
    if recent:
        _FAILED_LOGINS[key] = recent
    else:
        _FAILED_LOGINS.pop(key, None)
    return recent


def login_is_blocked(identifier):
    now = time.time()
    return len(_cleanup_attempts(identifier, now)) >= MAX_LOGIN_ATTEMPTS


def register_failed_login(identifier):
    now = time.time()
    _FAILED_LOGINS.setdefault(identifier, []).append(now)
    _cleanup_attempts(identifier, now)


def clear_failed_logins(identifier):
    _FAILED_LOGINS.pop(identifier, None)


# ----------------------------------------------------------------------------
# Usuário corrente e papéis
# ----------------------------------------------------------------------------
def current_user():
    return getattr(g, "user", None)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            flash("Faça login para acessar o sistema.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("login", next=request.path))
        if not user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def contar_admins_ativos():
    return Usuario.query.filter_by(papel="admin", ativo=True).count()


# ----------------------------------------------------------------------------
# Seed do administrador
# ----------------------------------------------------------------------------
def ensure_admin_user():
    """Garante que sempre exista um administrador ativo.

    Cria o admin apenas na primeira execução (nunca sobrescreve a senha de um
    usuário já existente). Se o admin já existe, apenas assegura papel/ativo —
    isso também promove corretamente um banco migrado de versões anteriores.
    """
    username = os.getenv("ADMIN_USER", "admin").strip() or "admin"
    password = os.getenv("ADMIN_PASSWORD", "").strip()

    user = Usuario.query.filter_by(username=username).first()
    if user:
        changed = False
        if user.papel != "admin":
            user.papel = "admin"
            changed = True
        if not user.ativo:
            user.ativo = True
            changed = True
        if not user.nome:
            user.nome = "Administrador"
            changed = True
        if changed:
            db.session.commit()
        return

    if not password:
        password = secrets.token_urlsafe(12)
        print(f"[seguranca] Admin '{username}' criado com senha temporaria: {password}")
    db.session.add(
        Usuario(
            username=username,
            nome="Administrador",
            papel="admin",
            password_hash=hash_senha(password),
            ativo=True,
        )
    )
    db.session.commit()


# ----------------------------------------------------------------------------
# Registro no app
# ----------------------------------------------------------------------------
def register_security(app):
    @app.context_processor
    def inject_context():
        return {"csrf_token": get_csrf_token, "current_user": current_user()}

    @app.before_request
    def guard():
        endpoint = request.endpoint
        if endpoint == "static" or endpoint is None:
            return None
        get_csrf_token()  # garante token na sessão para render dos formulários

        uid = session.get("user_id")
        user = db.session.get(Usuario, uid) if uid else None
        if user and not user.ativo:
            session.clear()
            user = None
        g.user = user

        if request.method not in SAFE_METHODS and not csrf_is_valid():
            abort(400, description="Token CSRF inválido ou ausente.")
        if endpoint not in PUBLIC_ENDPOINTS and user is None:
            return redirect(url_for("login", next=request.path))
        return None
