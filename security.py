"""Camada de segurança: sessão, papéis, CSRF, chave secreta, throttle e admin.

Autenticação por sessão e CSRF são implementados sem dependências externas,
mantendo a promessa de funcionar offline, em rede local fechada, após instalado.
"""

import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import abort, current_app, flash, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from models import Usuario, db


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Endpoints acessíveis sem login: entrada e recuperação de senha por código.
PUBLIC_ENDPOINTS = {"login", "static", "versao", "recuperar_senha"}
# Endpoints do assistente de configuração inicial (só acessíveis antes do setup).
SETUP_ENDPOINTS = {"setup"}
SENHA_MIN = 8
RECUPERACAO_TTL_MIN = 30

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
# Código de segurança da configuração inicial
# ----------------------------------------------------------------------------
# A rota /setup é gravável sem autenticação enquanto o primeiro admin não existe.
# Em rede local (deploy com --host=0.0.0.0), qualquer um poderia chegar primeiro
# e criar o administrador. O código de segurança fecha essa janela: ele é gerado
# no primeiro boot, impresso apenas no console do servidor e exigido no /setup —
# provando que quem configura tem acesso à máquina, não só à rede.
#
# Alfabeto sem caracteres ambíguos (0/O, 1/I/L) para leitura confiável no console.
SETUP_TOKEN_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
SETUP_TOKEN_GROUPS = 2
SETUP_TOKEN_GROUP_LEN = 4


def _setup_token_path(instance_path):
    return Path(instance_path) / "setup_token"


def _novo_codigo():
    bruto = "".join(
        secrets.choice(SETUP_TOKEN_ALPHABET)
        for _ in range(SETUP_TOKEN_GROUPS * SETUP_TOKEN_GROUP_LEN)
    )
    partes = [
        bruto[i : i + SETUP_TOKEN_GROUP_LEN]
        for i in range(0, len(bruto), SETUP_TOKEN_GROUP_LEN)
    ]
    return "-".join(partes)


def gerar_setup_token(instance_path):
    """Retorna o código de segurança do setup, criando e persistindo se ausente.

    Persiste em instance/setup_token para sobreviver a reinícios do servidor
    durante a configuração. Removido quando o setup conclui (clear_setup_token).
    """
    path = _setup_token_path(instance_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existente = path.read_text(encoding="utf-8").strip()
        if existente:
            return existente
    token = _novo_codigo()
    path.write_text(token, encoding="utf-8")
    return token


def setup_token_valido(instance_path, enviado):
    """Compara o código informado com o persistido, em tempo constante."""
    path = _setup_token_path(instance_path)
    if not path.exists():
        return False
    atual = path.read_text(encoding="utf-8").strip()
    if not atual:
        return False
    normalizado = str(enviado or "").strip().upper().replace(" ", "")
    return hmac.compare_digest(normalizado, atual.upper())


def clear_setup_token(instance_path):
    """Remove o código após a conclusão do setup — deixa de ser válido."""
    path = _setup_token_path(instance_path)
    if path.exists():
        path.unlink()


def setup_pendente():
    """Primeira execução: ainda não existe nenhum administrador ativo.

    Enquanto isto for verdadeiro, o sistema conduz o operador ao assistente de
    configuração inicial (/setup) em vez de exigir edição manual do .env.

    Latch de mão única em app.config: uma vez que exista um admin ativo, o
    sistema jamais volta a "pendente" (o último admin ativo não pode ser
    removido/desativado/rebaixado). Isso evita um COUNT por requisição no
    guard, que roda no before_request de toda rota.
    """
    if current_app.config.get("SETUP_DONE"):
        return False
    if contar_admins_ativos() == 0:
        return True
    current_app.config["SETUP_DONE"] = True
    return False


def criar_admin_inicial(username, nome, senha):
    """Cria o administrador da primeira execução, a partir do assistente /setup.

    Guarda de segurança essencial: só opera enquanto o setup está pendente. Uma
    vez que exista qualquer admin ativo, nunca mais cria — impede que a rota de
    setup seja reaberta para forjar um novo administrador.
    """
    if not setup_pendente():
        return None
    user = Usuario(
        username=username,
        nome=(nome or "").strip() or "Administrador",
        papel="admin",
        password_hash=hash_senha(senha),
        ativo=True,
    )
    db.session.add(user)
    db.session.commit()
    current_app.config["SETUP_DONE"] = True
    return user


# ----------------------------------------------------------------------------
# Recuperação de senha por código de console
# ----------------------------------------------------------------------------
# Mesma garantia do setup: o código nasce no terminal do servidor (comando
# `python app.py recuperar-senha <usuario>`) e é consumido na tela de login.
# Recuperar exige acesso à máquina — não basta estar na rede local. O registro
# guarda apenas o HASH do código (nunca o texto), o usuário-alvo e a validade.
def _recuperacao_path(instance_path):
    return Path(instance_path) / "recovery.json"


def gerar_codigo_recuperacao(instance_path, username):
    """Gera e persiste um código de recuperação de uso único para `username`.

    Retorna (codigo, minutos_de_validade). Só o hash é gravado em disco.
    """
    codigo = _novo_codigo()
    expira = datetime.now(timezone.utc) + timedelta(minutes=RECUPERACAO_TTL_MIN)
    payload = {
        "username": username,
        "code_hash": generate_password_hash(codigo.upper()),
        "expira_em": expira.isoformat(),
    }
    path = _recuperacao_path(instance_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return codigo, RECUPERACAO_TTL_MIN


def consumir_codigo_recuperacao(instance_path, codigo):
    """Valida o código (hash + expiração) e devolve o username-alvo, ou None.

    Não remove o registro: o chamador remove só após redefinir a senha com
    sucesso (clear_codigo_recuperacao), evitando queimar o código por engano.
    """
    path = _recuperacao_path(instance_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        expira = datetime.fromisoformat(data["expira_em"])
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        return None
    if datetime.now(timezone.utc) >= expira:
        return None
    if not check_password_hash(data.get("code_hash", ""), str(codigo or "").strip().upper()):
        return None
    return data.get("username")


def clear_codigo_recuperacao(instance_path):
    path = _recuperacao_path(instance_path)
    if path.exists():
        path.unlink()


# ----------------------------------------------------------------------------
# Seed do administrador
# ----------------------------------------------------------------------------
def ensure_admin_user():
    """Semeia o admin a partir de variáveis de ambiente (instalação automatizada).

    Caminho opcional, para quem provisiona sem interação (ADMIN_USER/ADMIN_PASSWORD).
    Sem uma senha explícita em ADMIN_PASSWORD, nada é criado: o assistente /setup
    assume a criação do primeiro administrador, com senha escolhida pelo operador.
    Nunca sobrescreve a senha de um usuário já existente. Quando o admin já existe,
    apenas assegura papel/ativo — promovendo corretamente bancos de versões antigas.
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
        # Sem credencial em ambiente: a tela /setup conduz a configuração inicial.
        return
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

        # Primeira execução: conduz todo o tráfego ao assistente de configuração.
        if setup_pendente():
            if endpoint in SETUP_ENDPOINTS or endpoint == "versao":
                if request.method not in SAFE_METHODS and not csrf_is_valid():
                    abort(400, description="Token CSRF inválido ou ausente.")
                return None
            return redirect(url_for("setup"))

        # Setup já concluído: o assistente não pode ser reaberto.
        if endpoint in SETUP_ENDPOINTS:
            return redirect(url_for("login"))

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
