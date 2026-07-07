from datetime import date, datetime, timedelta
import logging
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import event, inspect as sa_inspect, or_, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload
from werkzeug.security import check_password_hash

from domain import (
    ERCV_PENDENTE,
    PREVENT_STATUS,
    PREVENT_STATUS_VALUES,
    calcular_erg,
    calcular_findrisc,
    calculate_age,
    calculate_imc,
    calculate_prevent,
    comparar_risco,
    faixa_erg,
    faixa_findrisc,
    is_prevent_status,
    recalculate_cronico,
    recalculate_gestante,
    recalculate_idoso,
    sync_prevent_from_patient,
)
from models import (
    AgenteSaude,
    Auditoria,
    AvaliacaoPrevent,
    Gestante,
    HistoricoRisco,
    PacienteCronico,
    PacienteIdoso,
    Usuario,
    db,
)
from reports import get_report_rows, write_excel_report, write_pdf_report
from security import (
    SENHA_MIN,
    admin_required,
    clear_codigo_recuperacao,
    clear_failed_logins,
    clear_setup_token,
    consumir_codigo_recuperacao,
    contar_admins_ativos,
    criar_admin_inicial,
    current_user,
    ensure_admin_user,
    gerar_codigo_recuperacao,
    gerar_setup_token,
    hash_senha,
    login_is_blocked,
    register_failed_login,
    register_security,
    resolve_secret_key,
    senha_confere,
    senha_valida,
    setup_pendente,
    setup_token_valido,
)
from utils import (
    cpf_valido,
    date_br,
    format_cpf,
    only_digits,
    paginate_list,
    parse_date,
    parse_int,
    parse_float,
)

BASE_DIR = Path(__file__).resolve().parent
APP_VERSION = "2026-07-04-hardening"
PER_PAGE = 50


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("estratificacao")


def _log_banner_setup(token):
    """Imprime o código de segurança da configuração inicial no console do servidor.

    Nível WARNING para sobressair no log e não ser filtrado por INFO. É o único
    canal do código: ele nunca trafega pela rede antes de o operador informá-lo.
    """
    linhas = [
        "",
        "============================================================",
        "  CONFIGURACAO INICIAL PENDENTE",
        "  1. Abra no navegador:      http://<endereco-do-servidor>:5000/setup",
        f"  2. Codigo de seguranca:    {token}",
        "     (informe este codigo na tela para criar o administrador)",
        "============================================================",
        "",
    ]
    logger.warning("\n".join(linhas))


def _log_banner_recuperacao(username, codigo, ttl):
    """Imprime o codigo de recuperacao apenas no console/log do servidor."""
    linhas = [
        "",
        "============================================================",
        "  RECUPERACAO DE SENHA",
        f"  Usuario:               {username}",
        f"  Codigo de recuperacao: {codigo}   (valido por {ttl} min)",
        "  Informe este codigo na tela de redefinicao de senha.",
        "============================================================",
        "",
    ]
    logger.warning("\n".join(linhas))


def create_app():
    load_dotenv()
    app = Flask(__name__)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config["SECRET_KEY"] = resolve_secret_key(app.instance_path)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL", "sqlite:///estratificacao_risco.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    db.init_app(app)
    app.jinja_env.filters["cpf"] = format_cpf
    app.jinja_env.filters["date_br"] = date_br

    @app.context_processor
    def inject_agentes():
        # Lista de ACS ativos disponível em todos os templates (dropdowns).
        agentes = AgenteSaude.query.filter_by(ativo=True).order_by(AgenteSaude.nome).all()
        return {"agentes_saude": agentes}

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    with app.app_context():
        _register_sqlite_pragmas()
        db.create_all()
        ensure_schema()
        # Backfill de papel para bancos migrados de versões anteriores.
        Usuario.query.filter(Usuario.papel.is_(None)).update({Usuario.papel: "padrao"})
        db.session.commit()
        ensure_admin_user()
        # Primeira execução: emite o código de segurança do assistente /setup.
        if setup_pendente():
            _log_banner_setup(gerar_setup_token(app.instance_path))

    register_security(app)
    register_routes(app)
    return app


def _register_sqlite_pragmas():
    """WAL melhora concorrência de leitura/escrita; foreign_keys reforça integridade."""
    if db.engine.url.get_backend_name() != "sqlite":
        return

    @event.listens_for(db.engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def ensure_schema():
    """Auto-migração leve para SQLite: adiciona colunas novas em tabelas existentes.

    `create_all()` só cria tabelas ausentes — nunca altera as existentes. Isto fecha
    a lacuna de schema drift ao evoluir os modelos, sem exigir Alembic instalado.
    """
    inspector = sa_inspect(db.engine)
    for table_name, table in db.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue
        existing = {col["name"] for col in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name in existing:
                continue
            coltype = column.type.compile(dialect=db.engine.dialect)
            db.session.execute(
                text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {coltype}')
            )
    db.session.commit()

    # Cria indices que ainda nao existem (ex.: colunas indexadas adicionadas depois).
    for table in db.metadata.tables.values():
        for index in table.indexes:
            index.create(bind=db.engine, checkfirst=True)


# ----------------------------------------------------------------------------
# Persistência auxiliar
# ----------------------------------------------------------------------------
def commit_session():
    """Persiste tratando violação de unicidade (ex.: CPF duplicado) sem estourar 500."""
    try:
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False


def registrar_auditoria(acao, entidade, entidade_id=None, detalhe=""):
    """Registra operação de escrita para rastreabilidade (LGPD). Commit fica com o chamador."""
    db.session.add(
        Auditoria(
            usuario=session.get("username"),
            acao=acao,
            entidade=entidade,
            entidade_id=entidade_id,
            detalhe=(detalhe or "")[:200],
        )
    )


def registrar_historico_risco(tipo, paciente_id, risco_novo, risco_anterior, detalhe=""):
    """Registra uma mudança de estratificação na linha do tempo do paciente.

    Chamado APÓS o commit do paciente (quando o id já existe). Best-effort, em
    transação própria: uma falha aqui jamais compromete o salvamento principal.
    Só grava quando o risco realmente mudou.
    """
    if (risco_novo or "") == (risco_anterior or ""):
        return
    db.session.add(
        HistoricoRisco(
            tipo=tipo,
            paciente_id=paciente_id,
            risco=risco_novo or "—",
            risco_anterior=risco_anterior,
            tendencia=comparar_risco(risco_anterior, risco_novo),
            detalhe=(detalhe or "")[:200],
            autor=session.get("username"),
        )
    )
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logging.getLogger("estratificacao").exception(
            "Falha ao registrar histórico de risco (%s #%s)", tipo, paciente_id
        )


def detalhe_historico_cronico(paciente):
    if paciente.ercv_percentual is not None:
        return f"Risco cardiovascular em 10 anos: {paciente.ercv_percentual}%"
    return ""


def detalhe_historico_idoso(paciente):
    if paciente.ivcf_pontos is not None:
        return f"{paciente.ivcf_pontos} pontos no IVCF-20"
    return ""


def detalhe_historico_gestante(paciente):
    partes = []
    if paciente.imc:
        partes.append(f"IMC {paciente.imc}")
    if paciente.ig_atual_semanas:
        partes.append(f"IG {paciente.ig_atual_semanas}")
    return " · ".join(partes)


# Mapa dos três módulos para a linha do tempo: modelo, campo do nome, campo do
# risco atual e a rota de retorno da respectiva lista.
HISTORICO_TIPOS = {
    "cronico": (PacienteCronico, "nome_completo", "risco_estratificado", "lista_cronicos"),
    "idoso": (PacienteIdoso, "nome_completo", "estrato_clinico_funcional", "lista_idosos"),
    "gestante": (Gestante, "nome_paciente", "classificacao_risco", "lista_gestantes"),
}


def historico_valor(valor):
    if isinstance(valor, bool):
        return "Sim" if valor else "Não"
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y às %H:%M")
    if isinstance(valor, date):
        return date_br(valor)
    if valor is None or valor == "":
        return "Não informado"
    return valor


def historico_campo(rotulo, valor, automatico=False):
    return {
        "rotulo": rotulo,
        "valor": historico_valor(valor),
        "vazio": valor is None or valor == "",
        "automatico": automatico,
    }


def historico_secao(titulo, campos, descricao=""):
    return {"titulo": titulo, "descricao": descricao, "campos": campos}


def dados_historico_cronico(paciente):
    secoes = [
        historico_secao(
            "Dados pessoais",
            [
                historico_campo("Nome completo", paciente.nome_completo),
                historico_campo("CPF", format_cpf(paciente.cpf)),
                historico_campo("ACS", paciente.acs),
                historico_campo("Data de nascimento", paciente.data_nascimento),
                historico_campo("Idade", paciente.idade, automatico=True),
                historico_campo("Sexo", paciente.sexo),
            ],
        ),
        historico_secao(
            "Dados clínicos",
            [
                historico_campo("HAS", paciente.has),
                historico_campo("DM2", paciente.dm2),
                historico_campo("DM1", paciente.dm1),
                historico_campo("Pré-diabetes", paciente.pre_diabetes),
                historico_campo("Obesidade", paciente.obesidade),
                historico_campo("Tabagismo", paciente.tabagismo),
                historico_campo("Dislipidemia", paciente.dislipidemia),
                historico_campo("DRC", paciente.drc),
                historico_campo("LOA", paciente.loa),
                historico_campo("Doença aterosclerótica", paciente.doenca_aterosclerotica),
                historico_campo("Especialista", paciente.especialista),
                historico_campo("eMulti", paciente.emulti),
            ],
        ),
        historico_secao(
            "Sinais vitais e exames",
            [
                historico_campo("Última PA", paciente.ultima_pa),
                historico_campo("PAS", paciente.pas),
                historico_campo("PAD", paciente.pad),
                historico_campo("Data últ. PA", paciente.data_ult_pa),
                historico_campo("Última HbA1c", paciente.ultima_hba1c),
                historico_campo("Data últ. HbA1c", paciente.data_ult_hba1c),
                historico_campo("Peso", paciente.peso),
                historico_campo("Estatura", paciente.estatura),
                historico_campo("Escore CV (%)", paciente.ercv_percentual, automatico=True),
                historico_campo("Faixa ERCV", paciente.ercv_faixa),
                historico_campo("Base ERCV", paciente.ercv_base, automatico=True),
                historico_campo("Risco estratificado", paciente.risco_estratificado, automatico=True),
            ],
        ),
    ]
    if paciente.avaliacao_prevent:
        prevent = paciente.avaliacao_prevent
        secoes.append(
            historico_secao(
                "Avaliação PREVENT",
                [
                    historico_campo("Idade calc.", prevent.idade_cal_prevent, automatico=True),
                    historico_campo("Data exames", prevent.data_exames),
                    historico_campo("CT", prevent.ct),
                    historico_campo("HDL", prevent.hdl),
                    historico_campo("PAS", prevent.pas),
                    historico_campo("TFG CKD-EPI", prevent.tfg_ckd_epi),
                    historico_campo("Creatinina", prevent.cr),
                    historico_campo("LDL", prevent.ldl),
                    historico_campo("DM2", prevent.dm2),
                    historico_campo("Fumante", prevent.fumante),
                    historico_campo("Anti-hipertensivo", prevent.anti_hipertensivo),
                    historico_campo("Uso de estatina", prevent.uso_estatina),
                    historico_campo("log-Odds", prevent.log_odds, automatico=True),
                    historico_campo(
                        "Risco cardiovascular 10 anos",
                        prevent.risco_cardiovascular_10_anos,
                        automatico=True,
                    ),
                ],
                "Dados salvos na etapa PREVENT deste paciente.",
            )
        )
    return secoes


def dados_historico_gestante(paciente):
    return [
        historico_secao(
            "Dados pessoais",
            [
                historico_campo("Nome", paciente.nome_paciente),
                historico_campo("CPF", format_cpf(paciente.cpf)),
                historico_campo("ACS", paciente.acs),
                historico_campo("Data de nascimento", paciente.data_nascimento),
                historico_campo("Idade", paciente.idade, automatico=True),
                historico_campo("Raça/cor", paciente.raca_cor),
                historico_campo("Vulnerabilidade familiar", paciente.vulnerabilidade_familiar),
            ],
        ),
        historico_secao(
            "Pré-natal",
            [
                historico_campo("Última consulta", paciente.ultima_consulta),
                historico_campo("DUM", paciente.dum),
                historico_campo("1º USG", paciente.primeiro_usg),
                historico_campo("IG 1º USG", paciente.ig_primeiro_usg),
                historico_campo("IG semanas", paciente.ig_semanas),
                historico_campo("IG atual", paciente.ig_atual_semanas, automatico=True),
                historico_campo("DPP calculada", paciente.dpp, automatico=True),
                historico_campo("Nº de gestações", paciente.numero_gestacoes),
                historico_campo("Consulta regular UBS", paciente.consulta_regular_ubs),
                historico_campo("Avaliação odonto", paciente.avaliacao_odonto),
                historico_campo("Classificação de risco", paciente.classificacao_risco, automatico=True),
            ],
        ),
        historico_secao(
            "Triagem e exames",
            [
                historico_campo("Risco intermediário", paciente.criterio_risco_intermediario),
                historico_campo("Alto risco", paciente.criterio_alto_risco),
                historico_campo("HAC descontrole", paciente.hac_descontrole),
                historico_campo("DM descontrole", paciente.dm_descontrole),
                historico_campo("Teste da mamãe 1", paciente.teste_mamae_1),
                historico_campo("Teste da mamãe 2", paciente.teste_mamae_2),
                historico_campo("Exames rotina sangue", paciente.exames_rotina_sangue),
                historico_campo("Resultados", paciente.resultados),
            ],
        ),
        historico_secao(
            "Sinais vitais",
            [
                historico_campo("Peso kg", paciente.peso),
                historico_campo("Estatura cm", paciente.estatura),
                historico_campo("IMC calculado", paciente.imc, automatico=True),
                historico_campo("Glicemia capilar", paciente.glicemia_capilar),
                historico_campo("Respiração", paciente.respiracao),
                historico_campo("AFU", paciente.afu),
                historico_campo("PA", paciente.pa),
                historico_campo("BCF", paciente.bcf),
                historico_campo("Freq. cardíaca", paciente.freq_card),
            ],
        ),
    ]


def dados_historico_idoso(paciente):
    return [
        historico_secao(
            "Dados pessoais",
            [
                historico_campo("Nome completo", paciente.nome_completo),
                historico_campo("CPF", format_cpf(paciente.cpf)),
                historico_campo("ACS", paciente.acs),
                historico_campo("Data de nascimento", paciente.data_nascimento),
                historico_campo("Idade", paciente.idade, automatico=True),
                historico_campo("Sexo", paciente.sexo),
                historico_campo("Telefone", paciente.telefone),
            ],
        ),
        historico_secao(
            "IVCF-20",
            [
                historico_campo("Autopercepção ruim", paciente.ivcf_autopercepcao_ruim),
                historico_campo("Compras", paciente.ivcf_compras),
                historico_campo("Dinheiro", paciente.ivcf_dinheiro),
                historico_campo("Trabalho doméstico", paciente.ivcf_domestico),
                historico_campo("Banho", paciente.ivcf_banho),
                historico_campo("Esquecimento", paciente.ivcf_esquecimento),
                historico_campo("Esquecimento piorando", paciente.ivcf_esquecimento_piorando),
                historico_campo("Esquecimento impede rotina", paciente.ivcf_esquecimento_impede),
                historico_campo("Desânimo", paciente.ivcf_desanimo),
                historico_campo("Perda de interesse", paciente.ivcf_perda_interesse),
                historico_campo("Braços", paciente.ivcf_bracos),
                historico_campo("Objetos", paciente.ivcf_objetos),
                historico_campo("Capacidade aeróbica", paciente.ivcf_capacidade_aerobica),
                historico_campo("Marcha", paciente.ivcf_marcha),
                historico_campo("Quedas", paciente.ivcf_quedas),
                historico_campo("Incontinência", paciente.ivcf_incontinencia),
                historico_campo("Visão", paciente.ivcf_visao),
                historico_campo("Audição", paciente.ivcf_audicao),
                historico_campo("Comorbidades", paciente.ivcf_comorbidades),
            ],
        ),
        historico_secao(
            "Resultado e observações",
            [
                historico_campo("IVCF pontos", paciente.ivcf_pontos, automatico=True),
                historico_campo("Classificação IVCF", paciente.classificacao_ivcf, automatico=True),
                historico_campo("Estrato clínico-funcional", paciente.estrato_clinico_funcional, automatico=True),
                historico_campo("Observações", paciente.observacoes),
                historico_campo("Atualizado em", paciente.atualizado_em, automatico=True),
            ],
        ),
    ]


HISTORICO_DADOS = {
    "cronico": dados_historico_cronico,
    "idoso": dados_historico_idoso,
    "gestante": dados_historico_gestante,
}


# ----------------------------------------------------------------------------
# Aplicação de formulário (ligado ao request). Parsing/formatação vem de utils.py
# ----------------------------------------------------------------------------
def admin_master_id():
    """Primeiro administrador cadastrado, protegido como criador do sistema."""
    master = Usuario.query.filter_by(papel="admin").order_by(Usuario.id.asc()).first()
    return master.id if master else None


def checkbox(name):
    return name in request.form


def apply_form(obj, fields):
    for field, kind in fields.items():
        value = request.form.get(field)
        if kind == "date":
            value = parse_date(value)
        elif kind == "int":
            value = parse_int(value)
        elif kind == "float":
            value = parse_float(value)
        elif kind == "bool":
            value = checkbox(field)
        elif kind == "cpf":
            value = format_cpf(value)
        setattr(obj, field, value)

    if hasattr(obj, "data_nascimento") and hasattr(obj, "idade"):
        obj.idade = calculate_age(obj.data_nascimento)
    if hasattr(obj, "risco_estratificado") and is_prevent_status(obj.risco_estratificado):
        obj.risco_estratificado = PREVENT_STATUS


CRONICO_FIELDS = {
    "nome_completo": "str",
    "acs": "str",
    "data_nascimento": "date",
    "idade": "int",
    "sexo": "str",
    "cpf": "cpf",
    "has": "bool",
    "dm2": "bool",
    "dm1": "bool",
    "pre_diabetes": "bool",
    "dcv_at_sintomatica": "bool",
    "doenca_aterosclerotica": "bool",
    "loa": "bool",
    "drc": "bool",
    "ateroesclerose_subclinica": "bool",
    "complicacao_cronica": "bool",
    "internacao_aguda_12m": "bool",
    "tabagismo": "bool",
    "dislipidemia": "bool",
    "dcv_familiar_precoce": "bool",
    "obesidade": "bool",
    "autocuidado_suficiente": "bool",
    "controle_pressorico_adequado": "bool",
    "ercv_faixa": "str",
    "especialista": "str",
    "emulti": "bool",
    "risco_estratificado": "str",
    "exames_cardiovasc": "str",
    "ultima_hba1c": "float",
    "data_ult_hba1c": "date",
    "ultima_pa": "str",
    "pas": "int",
    "data_ult_pa": "date",
}

PREVENT_FIELDS = {
    "idade_cal_prevent": "int",
    "ct_calc": "float",
    "data_exames": "date",
    "ct": "float",
    "hdl_calc_prevent": "float",
    "hdl": "float",
    "pas_calc_prevent": "int",
    "tfg_cal_prevent": "float",
    "tfg_ckd_epi": "float",
    "cr": "float",
    "ldl": "float",
    "pas": "int",
    "dm2": "bool",
    "fumante": "bool",
    "anti_hipertensivo": "bool",
    "uso_estatina": "bool",
    "log_odds": "float",
    "risco_cardiovascular_10_anos": "str",
}

GESTANTE_FIELDS = {
    "nome_paciente": "str",
    "acs": "str",
    "ultima_consulta": "date",
    "grupo": "str",
    "cpf": "cpf",
    "data_nascimento": "date",
    "idade": "int",
    "raca_cor": "str",
    "vulnerabilidade_familiar": "str",
    "ig_semanas": "int",
    "consulta_regular_ubs": "str",
    "criterio_risco_intermediario": "bool",
    "criterio_alto_risco": "bool",
    "hac_descontrole": "bool",
    "dm_descontrole": "bool",
    "classificacao_risco": "str",
    "avaliacao_odonto": "str",
    "dum": "date",
    "primeiro_usg": "date",
    "ig_primeiro_usg": "str",
    "ig_atual_semanas": "str",
    "numero_gestacoes": "int",
    "teste_mamae_1": "bool",
    "teste_mamae_2": "bool",
    "exames_rotina_sangue": "bool",
    "resultados": "str",
    "peso": "float",
    "estatura": "float",
    "glicemia_capilar": "float",
    "respiracao": "int",
    "afu": "int",
    "pa": "str",
    "bcf": "int",
    "freq_card": "int",
}

IDOSO_FIELDS = {
    "nome_completo": "str",
    "cpf": "cpf",
    "acs": "str",
    "data_nascimento": "date",
    "idade": "int",
    "sexo": "str",
    "telefone": "str",
    "ivcf_autopercepcao_ruim": "bool",
    "ivcf_compras": "bool",
    "ivcf_dinheiro": "bool",
    "ivcf_domestico": "bool",
    "ivcf_banho": "bool",
    "ivcf_esquecimento": "bool",
    "ivcf_esquecimento_piorando": "bool",
    "ivcf_esquecimento_impede": "bool",
    "ivcf_desanimo": "bool",
    "ivcf_perda_interesse": "bool",
    "ivcf_bracos": "bool",
    "ivcf_objetos": "bool",
    "ivcf_capacidade_aerobica": "bool",
    "ivcf_marcha": "bool",
    "ivcf_quedas": "bool",
    "ivcf_incontinencia": "bool",
    "ivcf_visao": "bool",
    "ivcf_audicao": "bool",
    "ivcf_comorbidades": "bool",
    "observacoes": "str",
}


# ----------------------------------------------------------------------------
# Consultas de apoio
# ----------------------------------------------------------------------------
def count_by(model, column):
    return db.session.query(column, db.func.count(model.id)).group_by(column).all()


def risco_counts(rows):
    return {label or "Sem classificação": total for label, total in rows}


def report_filters():
    return {
        "tipo": request.args.get("tipo", "todos"),
        "risco": request.args.get("risco", "").strip(),
        "acs": request.args.get("acs", "").strip(),
    }


def all_risk_labels():
    rows = count_by(PacienteCronico, PacienteCronico.risco_estratificado)
    rows += count_by(Gestante, Gestante.classificacao_risco)
    rows += count_by(PacienteIdoso, PacienteIdoso.classificacao_ivcf)
    return sorted({label for label, _total in rows if label and not is_prevent_status(label)})


def agente_uso_map():
    """Conta quantos registros (crônicos + gestantes) referenciam cada ACS."""
    usos = {}
    for model in (PacienteCronico, Gestante, PacienteIdoso):
        rows = (
            db.session.query(model.acs, db.func.count(model.id))
            .filter(model.acs.isnot(None), model.acs != "")
            .group_by(model.acs)
            .all()
        )
        for nome, total in rows:
            usos[nome] = usos.get(nome, 0) + total
    return usos


def propagar_rename_agente(nome_antigo, nome_novo):
    """Mantém a integridade: renomear o ACS atualiza os registros vinculados."""
    for model in (PacienteCronico, Gestante, PacienteIdoso):
        model.query.filter(model.acs == nome_antigo).update(
            {model.acs: nome_novo}, synchronize_session=False
        )


# get_report_rows, write_excel_report e write_pdf_report ficam em reports.py


# ----------------------------------------------------------------------------
# Rotas
# ----------------------------------------------------------------------------
def register_routes(app):
    @app.route("/versao")
    def versao():
        return f"Estratificacao de Risco - versao {APP_VERSION}", 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        # Defesa em profundidade: mesmo que o guard falhe, nunca configura duas vezes.
        if not setup_pendente():
            return redirect(url_for("login"))
        nome = (request.form.get("nome") or "").strip()
        username = (request.form.get("username") or "").strip()
        codigo = (request.form.get("codigo") or "").strip()
        if request.method == "POST":
            senha = request.form.get("senha") or ""
            confirma = request.form.get("confirmar_senha") or ""

            # Trava de força-bruta no código de segurança (endpoint sem auth).
            identificador = f"{request.remote_addr}|setup"
            if login_is_blocked(identificador):
                flash("Muitas tentativas. Aguarde alguns minutos e tente novamente.", "danger")
                return render_template("setup.html", nome=nome, username=username, codigo="")
            if not setup_token_valido(app.instance_path, codigo):
                register_failed_login(identificador)
                flash(
                    "Código de segurança inválido. Confira o código exibido no console do servidor.",
                    "danger",
                )
                return render_template("setup.html", nome=nome, username=username, codigo="")

            erros = []
            if not username:
                erros.append("Informe um nome de usuário para o login.")
            elif Usuario.query.filter(
                db.func.lower(Usuario.username) == username.lower()
            ).first():
                erros.append(f"O usuário “{username}” já existe. Escolha outro.")
            if not senha_valida(senha):
                erros.append(f"A senha deve ter ao menos {SENHA_MIN} caracteres.")
            if senha != confirma:
                erros.append("A confirmação de senha não corresponde à senha digitada.")
            if erros:
                for erro in erros:
                    flash(erro, "warning")
                return render_template("setup.html", nome=nome, username=username, codigo=codigo)

            admin = criar_admin_inicial(username, nome, senha)
            if admin is None:
                flash("O sistema já foi configurado. Faça login normalmente.", "warning")
                return redirect(url_for("login"))
            clear_failed_logins(identificador)
            clear_setup_token(app.instance_path)
            registrar_auditoria(
                "setup", "sistema", admin.id, f"administrador inicial: {admin.username}"
            )
            db.session.commit()
            flash(
                "Configuração concluída! Entre com o usuário e a senha que você acabou de criar.",
                "success",
            )
            return redirect(url_for("login"))
        return render_template("setup.html", nome=nome, username=username, codigo=codigo)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("user_id"):
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            identifier = f"{request.remote_addr}|{username.lower()}"
            if login_is_blocked(identifier):
                flash("Muitas tentativas. Aguarde alguns minutos e tente novamente.", "danger")
                return render_template("login.html")
            user = Usuario.query.filter_by(username=username, ativo=True).first()
            if user and check_password_hash(user.password_hash, password):
                clear_failed_logins(identifier)
                session.clear()
                session["user_id"] = user.id
                session["username"] = user.username
                registrar_auditoria("login", "usuario", user.id, user.username)
                db.session.commit()
                flash("Login realizado com sucesso.", "success")
                return redirect(request.args.get("next") or url_for("dashboard"))
            register_failed_login(identifier)
            flash("Usuário ou senha inválidos.", "danger")
        return render_template("login.html")

    @app.route("/recuperar-senha", methods=["GET", "POST"])
    def recuperar_senha():
        if session.get("user_id"):
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            action = request.form.get("action") or "resetar"
            if action == "gerar_codigo":
                username = (request.form.get("username") or "").strip()
                identificador = f"{request.remote_addr}|recuperar-gerar"
                if login_is_blocked(identificador):
                    flash("Muitas tentativas. Aguarde alguns minutos e tente novamente.", "danger")
                    return render_template("recuperar.html", username=username)
                if not username:
                    flash("Informe o usuário de login para enviar o código ao terminal.", "warning")
                    return render_template("recuperar.html", username=username)

                register_failed_login(identificador)
                user = Usuario.query.filter(
                    db.func.lower(Usuario.username) == username.lower(),
                    Usuario.ativo.is_(True),
                ).first()
                if user:
                    codigo, ttl = gerar_codigo_recuperacao(app.instance_path, user.username)
                    _log_banner_recuperacao(user.username, codigo, ttl)
                else:
                    # Equaliza o tempo de resposta: sem isso, só o caminho do usuário
                    # existente roda o hash lento, permitindo enumeração por timing.
                    hash_senha("timing-equalizer")
                flash(
                    "Se o usuário existir e estiver ativo, o código foi enviado ao terminal do servidor.",
                    "success",
                )
                return render_template("recuperar.html", username=username)

            codigo = (request.form.get("codigo") or "").strip()
            senha = request.form.get("senha") or ""
            confirma = request.form.get("confirmar_senha") or ""

            # Trava de força-bruta no código (endpoint público, sem auth).
            identificador = f"{request.remote_addr}|recuperar"
            if login_is_blocked(identificador):
                flash("Muitas tentativas. Aguarde alguns minutos e tente novamente.", "danger")
                return render_template("recuperar.html")

            alvo = consumir_codigo_recuperacao(app.instance_path, codigo)
            if not alvo:
                register_failed_login(identificador)
                flash(
                    "Código inválido ou expirado. Solicite um novo pelo botão acima "
                    "(ele é enviado ao terminal do servidor).",
                    "danger",
                )
                return render_template("recuperar.html")

            if not senha_valida(senha):
                flash(f"A nova senha deve ter ao menos {SENHA_MIN} caracteres.", "warning")
                return render_template("recuperar.html", codigo=codigo)
            if senha != confirma:
                flash("A confirmação de senha não corresponde à senha digitada.", "warning")
                return render_template("recuperar.html", codigo=codigo)

            user = Usuario.query.filter(
                db.func.lower(Usuario.username) == alvo.lower()
            ).first()
            if not user:
                # Usuário removido depois da emissão do código: invalida e aborta.
                clear_codigo_recuperacao(app.instance_path)
                flash("A conta associada a este código não existe mais.", "danger")
                return redirect(url_for("login"))

            user.password_hash = hash_senha(senha)
            clear_failed_logins(identificador)
            clear_codigo_recuperacao(app.instance_path)
            registrar_auditoria("recuperacao", "usuario", user.id, user.username)
            db.session.commit()
            flash("Senha redefinida com sucesso. Entre com a nova senha.", "success")
            return redirect(url_for("login"))
        return render_template("recuperar.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("Sessão encerrada.", "success")
        return redirect(url_for("login"))

    @app.route("/")
    def dashboard():
        acs = request.args.get("acs", "").strip()

        cronicos_query = PacienteCronico.query
        gestantes_query = Gestante.query
        idosos_query = PacienteIdoso.query
        if acs:
            cronicos_query = cronicos_query.filter(PacienteCronico.acs == acs)
            gestantes_query = gestantes_query.filter(Gestante.acs == acs)
            idosos_query = idosos_query.filter(PacienteIdoso.acs == acs)

        total_cronicos = cronicos_query.count()
        total_gestantes = gestantes_query.count()
        total_idosos = idosos_query.count()
        riscos_cronicos = [
            row
            for row in cronicos_query.with_entities(
                PacienteCronico.risco_estratificado,
                db.func.count(PacienteCronico.id),
            )
            .group_by(PacienteCronico.risco_estratificado)
            .all()
            if not is_prevent_status(row[0]) and row[0] != ERCV_PENDENTE
        ]
        riscos_gestantes = (
            gestantes_query.with_entities(
                Gestante.classificacao_risco,
                db.func.count(Gestante.id),
            )
            .group_by(Gestante.classificacao_risco)
            .all()
        )
        ercv_pendente = cronicos_query.filter(
            PacienteCronico.risco_estratificado == ERCV_PENDENTE
        ).count()
        hoje = date.today()
        proximas_dpps = (
            gestantes_query.filter(
                Gestante.dpp >= hoje,
                Gestante.dpp <= hoje + timedelta(days=30),
            )
            .order_by(Gestante.dpp)
            .limit(5)
            .all()
        )
        cronicos_alerta = (
            cronicos_query.filter(
                or_(
                    PacienteCronico.risco_estratificado.ilike("%alto%"),
                    PacienteCronico.risco_estratificado == ERCV_PENDENTE,
                )
            )
            .order_by(PacienteCronico.nome_completo)
            .limit(6)
            .all()
        )
        gestantes_alto_risco = gestantes_query.filter(
            or_(
                Gestante.classificacao_risco.ilike("%alto%"),
                Gestante.criterio_alto_risco.is_(True),
            )
        ).count()
        idosos_frageis = idosos_query.filter(
            PacienteIdoso.classificacao_ivcf.ilike("%fr\u00e1gil%")
        ).count()

        comorb_defs = [
            ("HAS", PacienteCronico.has),
            ("DM2", PacienteCronico.dm2),
            ("DM1", PacienteCronico.dm1),
            ("Pr\u00e9-diabetes", PacienteCronico.pre_diabetes),
            ("Obesidade", PacienteCronico.obesidade),
            ("Tabagismo", PacienteCronico.tabagismo),
            ("Dislipidemia", PacienteCronico.dislipidemia),
            ("DRC", PacienteCronico.drc),
            ("LOA", PacienteCronico.loa),
            ("DCV/aterosclerose", PacienteCronico.doenca_aterosclerotica),
        ]
        comorbidades = [
            {"label": label, "total": cronicos_query.filter(column.is_(True)).count()}
            for label, column in comorb_defs
        ]
        max_comorbidades = max([item["total"] for item in comorbidades] + [1])

        acs_nomes = sorted(
            {
                nome
                for (nome,) in db.session.query(PacienteCronico.acs)
                .filter(PacienteCronico.acs.isnot(None), PacienteCronico.acs != "")
                .distinct()
                .all()
            }
            | {
                nome
                for (nome,) in db.session.query(Gestante.acs)
                .filter(Gestante.acs.isnot(None), Gestante.acs != "")
                .distinct()
                .all()
            }
            | {
                nome
                for (nome,) in db.session.query(PacienteIdoso.acs)
                .filter(PacienteIdoso.acs.isnot(None), PacienteIdoso.acs != "")
                .distinct()
                .all()
            }
            | {ag.nome for ag in AgenteSaude.query.filter_by(ativo=True).all()}
        )
        agentes_map = {ag.nome: ag for ag in AgenteSaude.query.all()}
        areas_acs = []
        for nome in acs_nomes:
            area_cronicos = PacienteCronico.query.filter(PacienteCronico.acs == nome)
            area_gestantes = Gestante.query.filter(Gestante.acs == nome)
            area_idosos = PacienteIdoso.query.filter(PacienteIdoso.acs == nome)
            total_area_cronicos = area_cronicos.count()
            total_area_gestantes = area_gestantes.count()
            total_area_idosos = area_idosos.count()
            total_area = total_area_cronicos + total_area_gestantes + total_area_idosos
            carga_comorbidades = sum(
                area_cronicos.filter(column.is_(True)).count()
                for _label, column in comorb_defs
            )
            alto_risco = (
                area_cronicos.filter(
                    PacienteCronico.risco_estratificado.ilike("%alto%")
                ).count()
                + area_gestantes.filter(
                    or_(
                        Gestante.classificacao_risco.ilike("%alto%"),
                        Gestante.criterio_alto_risco.is_(True),
                    )
                ).count()
                + area_idosos.filter(
                    PacienteIdoso.classificacao_ivcf.ilike("%fr\u00e1gil%")
                ).count()
            )
            agente = agentes_map.get(nome)
            areas_acs.append(
                {
                    "nome": nome,
                    "micro_area": agente.micro_area if agente else "",
                    "equipe": agente.equipe if agente else "",
                    "total": total_area,
                    "cronicos": total_area_cronicos,
                    "gestantes": total_area_gestantes,
                    "idosos": total_area_idosos,
                    "comorbidades": carga_comorbidades,
                    "alto_risco": alto_risco,
                }
            )
        areas_acs.sort(key=lambda item: (item["micro_area"] or "ZZZ", item["nome"]))
        max_area_total = max([item["total"] for item in areas_acs] + [1])
        max_area_comorbidades = max([item["comorbidades"] for item in areas_acs] + [1])
        acs_info = AgenteSaude.query.filter_by(nome=acs).first() if acs else None
        area_atual = next(
            (item for item in areas_acs if acs and item["nome"] == acs),
            None,
        )
        areas_base = [area_atual] if area_atual else areas_acs
        total_comorbidades_area = sum(item["comorbidades"] for item in areas_base)
        total_alto_risco_area = sum(item["alto_risco"] for item in areas_base)
        total_registros_area = sum(item["total"] for item in areas_base)
        media_registros_area = (
            round(total_registros_area / len(areas_base), 1) if areas_base else 0
        )
        area_maior_comorbidade = max(
            areas_base,
            key=lambda item: (item["comorbidades"], item["alto_risco"], item["total"]),
            default=None,
        )
        areas_destaque = sorted(
            areas_base,
            key=lambda item: (item["comorbidades"], item["alto_risco"], item["total"]),
            reverse=True,
        )[:4]

        return render_template(
            "dashboard.html",
            acs_sel=acs,
            acs_info=acs_info,
            total_cronicos=total_cronicos,
            total_gestantes=total_gestantes,
            total_idosos=total_idosos,
            riscos_cronicos=riscos_cronicos,
            riscos_gestantes=riscos_gestantes,
            riscos_cronicos_map=risco_counts(riscos_cronicos),
            riscos_gestantes_map=risco_counts(riscos_gestantes),
            ercv_pendente=ercv_pendente,
            total_has=cronicos_query.filter_by(has=True).count(),
            total_dm2=cronicos_query.filter_by(dm2=True).count(),
            gestantes_alto_risco=gestantes_alto_risco,
            idosos_frageis=idosos_frageis,
            comorbidades=comorbidades,
            max_comorbidades=max_comorbidades,
            areas_acs=areas_acs,
            max_area_total=max_area_total,
            max_area_comorbidades=max_area_comorbidades,
            area_atual=area_atual,
            areas_destaque=areas_destaque,
            total_comorbidades_area=total_comorbidades_area,
            total_alto_risco_area=total_alto_risco_area,
            media_registros_area=media_registros_area,
            area_maior_comorbidade=area_maior_comorbidade,
            proximas_dpps=proximas_dpps,
            cronicos_alerta=cronicos_alerta,
        )

    @app.route("/cronicos")
    def lista_cronicos():
        busca = request.args.get("q", "").strip()
        acs = request.args.get("acs", "").strip()
        risco = request.args.get("risco", "").strip()
        query = PacienteCronico.query
        if busca:
            busca_digits = only_digits(busca)
            filters = [
                PacienteCronico.nome_completo.ilike(f"%{busca}%"),
                PacienteCronico.cpf.ilike(f"%{busca}%"),
            ]
            if busca_digits:
                filters.append(PacienteCronico.cpf.ilike(f"%{busca_digits}%"))
            query = query.filter(or_(*filters))
        if acs:
            query = query.filter(PacienteCronico.acs == acs)
        if risco:
            query = query.filter(PacienteCronico.risco_estratificado == risco)
        page = request.args.get("page", 1, type=int)
        paginacao = db.paginate(
            query.options(joinedload(PacienteCronico.avaliacao_prevent)).order_by(
                PacienteCronico.nome_completo
            ),
            page=page,
            per_page=PER_PAGE,
            error_out=False,
        )
        riscos_disponiveis = sorted(
            risco
            for (risco,) in db.session.query(PacienteCronico.risco_estratificado)
            .filter(PacienteCronico.risco_estratificado.isnot(None))
            .distinct()
            .all()
            if risco
        )
        return render_template(
            "lista_cronicos.html",
            pacientes=paginacao.items,
            paginacao=paginacao,
            busca=busca,
            acs_sel=acs,
            risco_sel=risco,
            riscos_disponiveis=riscos_disponiveis,
        )

    @app.route("/cronicos/novo", methods=["GET", "POST"])
    def novo_cronico():
        paciente = PacienteCronico()
        if request.method == "POST":
            apply_form(paciente, CRONICO_FIELDS)
            if not cpf_valido(paciente.cpf):
                flash("CPF inválido. Informe os 11 dígitos.", "warning")
                return render_template("form_cronico.html", paciente=paciente)
            recalculate_cronico(paciente)
            db.session.add(paciente)
            registrar_auditoria("criar", "cronico", detalhe=paciente.nome_completo)
            if commit_session():
                registrar_historico_risco(
                    "cronico", paciente.id, paciente.risco_estratificado, None,
                    detalhe_historico_cronico(paciente),
                )
                flash("Paciente crônico cadastrado com sucesso.", "success")
                return redirect(url_for("lista_cronicos"))
            flash("Não foi possível salvar: já existe um paciente com este CPF.", "danger")
        return render_template("form_cronico.html", paciente=paciente)

    @app.route("/cronicos/editar/<int:id>", methods=["GET", "POST"])
    def editar_cronico(id):
        paciente = PacienteCronico.query.get_or_404(id)
        if request.method == "POST":
            risco_anterior = paciente.risco_estratificado
            apply_form(paciente, CRONICO_FIELDS)
            if not cpf_valido(paciente.cpf):
                flash("CPF inválido. Informe os 11 dígitos.", "warning")
                return render_template("form_cronico.html", paciente=paciente)
            recalculate_cronico(paciente)
            registrar_auditoria("editar", "cronico", paciente.id, paciente.nome_completo)
            if commit_session():
                registrar_historico_risco(
                    "cronico", paciente.id, paciente.risco_estratificado, risco_anterior,
                    detalhe_historico_cronico(paciente),
                )
                flash("Paciente crônico atualizado com sucesso.", "success")
                return redirect(url_for("lista_cronicos"))
            flash("Não foi possível salvar: já existe um paciente com este CPF.", "danger")
        return render_template("form_cronico.html", paciente=paciente)

    @app.route("/cronicos/excluir/<int:id>", methods=["POST"])
    def excluir_cronico(id):
        paciente = PacienteCronico.query.get_or_404(id)
        registrar_auditoria("excluir", "cronico", paciente.id, paciente.nome_completo)
        db.session.delete(paciente)
        db.session.commit()
        flash("Paciente crônico removido.", "warning")
        return redirect(url_for("lista_cronicos"))

    @app.route("/idosos")
    def lista_idosos():
        busca = request.args.get("q", "").strip()
        acs = request.args.get("acs", "").strip()
        risco = request.args.get("risco", "").strip()
        query = PacienteIdoso.query
        if busca:
            busca_digits = only_digits(busca)
            filters = [
                PacienteIdoso.nome_completo.ilike(f"%{busca}%"),
                PacienteIdoso.cpf.ilike(f"%{busca}%"),
            ]
            if busca_digits:
                filters.append(PacienteIdoso.cpf.ilike(f"%{busca_digits}%"))
            query = query.filter(or_(*filters))
        if acs:
            query = query.filter(PacienteIdoso.acs == acs)
        if risco:
            query = query.filter(PacienteIdoso.classificacao_ivcf == risco)
        page = request.args.get("page", 1, type=int)
        paginacao = db.paginate(
            query.order_by(PacienteIdoso.nome_completo),
            page=page,
            per_page=PER_PAGE,
            error_out=False,
        )
        riscos = [
            "Baixo risco de vulnerabilidade clínico-funcional",
            "Moderado risco de vulnerabilidade clínico-funcional",
            "Alto risco de vulnerabilidade clínico-funcional",
        ]
        return render_template(
            "lista_idosos.html",
            pacientes=paginacao.items,
            paginacao=paginacao,
            busca=busca,
            acs_sel=acs,
            risco_sel=risco,
            riscos=riscos,
        )

    @app.route("/idosos/novo", methods=["GET", "POST"])
    def novo_idoso():
        paciente = PacienteIdoso()
        if request.method == "POST":
            apply_form(paciente, IDOSO_FIELDS)
            if not cpf_valido(paciente.cpf):
                flash("CPF inválido. Informe os 11 dígitos.", "warning")
                return render_template("form_idoso.html", paciente=paciente)
            recalculate_idoso(paciente)
            db.session.add(paciente)
            registrar_auditoria("criar", "idoso", detalhe=paciente.nome_completo)
            if commit_session():
                registrar_historico_risco(
                    "idoso", paciente.id, paciente.estrato_clinico_funcional, None,
                    detalhe_historico_idoso(paciente),
                )
                flash("Pessoa idosa cadastrada com sucesso.", "success")
                return redirect(url_for("lista_idosos"))
            flash("Não foi possível salvar: já existe um paciente com este CPF.", "danger")
        return render_template("form_idoso.html", paciente=paciente)

    @app.route("/idosos/editar/<int:id>", methods=["GET", "POST"])
    def editar_idoso(id):
        paciente = PacienteIdoso.query.get_or_404(id)
        if request.method == "POST":
            risco_anterior = paciente.estrato_clinico_funcional
            apply_form(paciente, IDOSO_FIELDS)
            if not cpf_valido(paciente.cpf):
                flash("CPF inválido. Informe os 11 dígitos.", "warning")
                return render_template("form_idoso.html", paciente=paciente)
            recalculate_idoso(paciente)
            registrar_auditoria("editar", "idoso", paciente.id, paciente.nome_completo)
            if commit_session():
                registrar_historico_risco(
                    "idoso", paciente.id, paciente.estrato_clinico_funcional, risco_anterior,
                    detalhe_historico_idoso(paciente),
                )
                flash("Pessoa idosa atualizada com sucesso.", "success")
                return redirect(url_for("lista_idosos"))
            flash("Não foi possível salvar: já existe um paciente com este CPF.", "danger")
        return render_template("form_idoso.html", paciente=paciente)

    @app.route("/idosos/excluir/<int:id>", methods=["POST"])
    def excluir_idoso(id):
        paciente = PacienteIdoso.query.get_or_404(id)
        registrar_auditoria("excluir", "idoso", paciente.id, paciente.nome_completo)
        db.session.delete(paciente)
        db.session.commit()
        flash("Registro de pessoa idosa removido.", "warning")
        return redirect(url_for("lista_idosos"))

    @app.route("/findrisc", methods=["GET", "POST"])
    def findrisc():
        # Calculadora avulsa (opcional): risco de desenvolver DM2 em 10 anos.
        resultado = None
        dados = {}
        if request.method == "POST":
            dados = request.form
            imc = calculate_imc(
                parse_float(request.form.get("peso")),
                parse_float(request.form.get("estatura")),
            )
            pontos = calcular_findrisc(
                parse_int(request.form.get("idade")),
                imc,
                parse_float(request.form.get("cintura")),
                request.form.get("sexo"),
                atividade_fisica=("atividade_fisica" in request.form),
                come_vegetais_diario=("come_vegetais" in request.form),
                medicamento_pressao=("medicamento_pressao" in request.form),
                glicemia_alta=("glicemia_alta" in request.form),
                familiar_diabetes=request.form.get("familiar_diabetes"),
            )
            if pontos is None:
                flash(
                    "Para calcular, informe idade, peso, altura e circunferência da cintura.",
                    "warning",
                )
            else:
                resultado = {
                    "pontos": pontos,
                    "faixa": faixa_findrisc(pontos),
                    "imc": round(imc, 1) if imc else None,
                }
        return render_template("form_findrisc.html", resultado=resultado, dados=dados)

    @app.route("/metodologia")
    def metodologia():
        # Documentação técnica: como cada cálculo foi arquitetado, com exemplos,
        # e as notas técnicas oficiais (SES-GO) disponíveis para download.
        return render_template("metodologia.html")

    @app.route("/pacientes/<tipo>/<int:id>/historico")
    def historico_paciente(tipo, id):
        # Linha do tempo da estratificação de um paciente (evolução do risco).
        config = HISTORICO_TIPOS.get(tipo)
        if not config:
            abort(404)
        modelo, campo_nome, campo_risco, lista_endpoint = config
        paciente = modelo.query.get_or_404(id)
        registros = (
            HistoricoRisco.query.filter_by(tipo=tipo, paciente_id=id)
            .order_by(HistoricoRisco.criado_em.desc(), HistoricoRisco.id.desc())
            .limit(200)
            .all()
        )
        return render_template(
            "historico.html",
            tipo=tipo,
            nome=getattr(paciente, campo_nome),
            paciente_id=id,
            risco_atual=getattr(paciente, campo_risco),
            secoes_dados=HISTORICO_DADOS[tipo](paciente),
            registros=registros,
            lista_endpoint=lista_endpoint,
        )

    @app.route("/cronicos/<int:id>/prevent", methods=["GET", "POST"])
    def prevent(id):
        paciente = PacienteCronico.query.get_or_404(id)
        avaliacao = paciente.avaliacao_prevent or AvaliacaoPrevent()
        if request.method == "POST":
            apply_form(avaliacao, PREVENT_FIELDS)
            avaliacao.paciente = paciente
            db.session.add(avaliacao)
            calculate_prevent(avaliacao, paciente)
            recalculate_cronico(paciente)
            registrar_auditoria("prevent", "cronico", paciente.id, paciente.nome_completo)
            db.session.commit()
            flash("Avaliação PREVENT salva com sucesso.", "success")
            return redirect(url_for("lista_cronicos"))
        sync_prevent_from_patient(avaliacao, paciente)
        return render_template("form_prevent.html", paciente=paciente, avaliacao=avaliacao)

    @app.route("/cronicos/<int:id>/erg", methods=["GET", "POST"])
    def calcular_risco_erg(id):
        paciente = PacienteCronico.query.get_or_404(id)
        # Reaproveita a avaliação (colesterol, HDL, PAS, fumante, anti-HAS são os
        # mesmos insumos do PREVENT), evitando redigitar exames.
        avaliacao = paciente.avaliacao_prevent or AvaliacaoPrevent()
        if request.method == "POST":
            avaliacao.ct = parse_float(request.form.get("ct"))
            avaliacao.hdl = parse_float(request.form.get("hdl"))
            avaliacao.pas = parse_int(request.form.get("pas")) or paciente.pas
            avaliacao.fumante = "fumante" in request.form
            avaliacao.anti_hipertensivo = "anti_hipertensivo" in request.form
            avaliacao.paciente = paciente
            db.session.add(avaliacao)

            diabetes = bool(paciente.dm2 or paciente.dm1)
            risco = calcular_erg(
                paciente.sexo, paciente.idade, avaliacao.ct, avaliacao.hdl,
                avaliacao.pas, avaliacao.anti_hipertensivo, avaliacao.fumante, diabetes,
            )
            if risco is None:
                flash(
                    "Para calcular, informe colesterol total, HDL e PAS, e confira "
                    "idade e sexo do paciente.",
                    "warning",
                )
                return render_template("form_erg.html", paciente=paciente, avaliacao=avaliacao)

            paciente.ercv_percentual = round(risco * 100, 1)
            paciente.ercv_faixa = faixa_erg(risco, paciente.sexo)
            recalculate_cronico(paciente)
            registrar_auditoria(
                "erg", "cronico", paciente.id,
                f"{paciente.ercv_percentual}% ({paciente.ercv_faixa})",
            )
            db.session.commit()
            flash(
                f"Risco cardiovascular em 10 anos: {paciente.ercv_percentual}% — "
                f"faixa “{paciente.ercv_faixa}”. Estratificação atualizada.",
                "success",
            )
            return redirect(url_for("lista_cronicos"))
        return render_template("form_erg.html", paciente=paciente, avaliacao=avaliacao)

    @app.route("/prevent")
    def lista_prevent():
        acs = request.args.get("acs", "").strip()
        status = request.args.get("status", "").strip()
        prevent_calculado = PacienteCronico.avaliacao_prevent.has(
            AvaliacaoPrevent.risco_cardiovascular_10_anos.like("%!%%", escape="!")
        )
        prevent_nao_aplicavel = PacienteCronico.avaliacao_prevent.has(
            AvaliacaoPrevent.risco_cardiovascular_10_anos == "Não aplicável"
        )
        prevent_pendente = (
            ~prevent_calculado
            & ~prevent_nao_aplicavel
        )
        query = PacienteCronico.query
        if acs:
            query = query.filter(PacienteCronico.acs == acs)
        if status == "pendente":
            query = query.filter(prevent_pendente)
        elif status == "calculado":
            query = query.filter(prevent_calculado)
        # joinedload evita N+1 ao acessar avaliacao_prevent linha a linha no template.
        query = query.options(
            joinedload(PacienteCronico.avaliacao_prevent)
        ).order_by(PacienteCronico.nome_completo)
        page = request.args.get("page", 1, type=int)
        paginacao = db.paginate(query, page=page, per_page=PER_PAGE, error_out=False)
        total_query = PacienteCronico.query
        if acs:
            total_query = total_query.filter(PacienteCronico.acs == acs)
        total_calculados = total_query.filter(prevent_calculado).count()
        total_pendentes = total_query.filter(prevent_pendente).count()
        return render_template(
            "lista_prevent.html",
            pacientes=paginacao.items,
            paginacao=paginacao,
            acs_sel=acs,
            status_sel=status,
            total_calculados=total_calculados,
            total_pendentes=total_pendentes,
        )

    @app.route("/gestantes")
    def lista_gestantes():
        busca = request.args.get("q", "").strip()
        acs = request.args.get("acs", "").strip()
        query = Gestante.query
        if busca:
            busca_digits = only_digits(busca)
            filters = [
                Gestante.nome_paciente.ilike(f"%{busca}%"),
                Gestante.cpf.ilike(f"%{busca}%"),
            ]
            if busca_digits:
                filters.append(Gestante.cpf.ilike(f"%{busca_digits}%"))
            query = query.filter(or_(*filters))
        if acs:
            query = query.filter(Gestante.acs == acs)
        page = request.args.get("page", 1, type=int)
        paginacao = db.paginate(
            query.order_by(Gestante.nome_paciente),
            page=page,
            per_page=PER_PAGE,
            error_out=False,
        )
        return render_template(
            "lista_gestantes.html",
            pacientes=paginacao.items,
            paginacao=paginacao,
            busca=busca,
            acs_sel=acs,
        )

    @app.route("/gestantes/novo", methods=["GET", "POST"])
    def nova_gestante():
        paciente = Gestante(grupo="Gestante")
        if request.method == "POST":
            apply_form(paciente, GESTANTE_FIELDS)
            if not cpf_valido(paciente.cpf):
                flash("CPF inválido. Informe os 11 dígitos.", "warning")
                return render_template("form_gestante.html", paciente=paciente, hoje=date.today())
            recalculate_gestante(paciente)
            db.session.add(paciente)
            registrar_auditoria("criar", "gestante", detalhe=paciente.nome_paciente)
            if commit_session():
                registrar_historico_risco(
                    "gestante", paciente.id, paciente.classificacao_risco, None,
                    detalhe_historico_gestante(paciente),
                )
                flash("Gestante cadastrada com sucesso.", "success")
                return redirect(url_for("lista_gestantes"))
            flash("Não foi possível salvar: já existe um registro com este CPF.", "danger")
        return render_template("form_gestante.html", paciente=paciente, hoje=date.today())

    @app.route("/gestantes/editar/<int:id>", methods=["GET", "POST"])
    def editar_gestante(id):
        paciente = Gestante.query.get_or_404(id)
        if request.method == "POST":
            risco_anterior = paciente.classificacao_risco
            apply_form(paciente, GESTANTE_FIELDS)
            if not cpf_valido(paciente.cpf):
                flash("CPF inválido. Informe os 11 dígitos.", "warning")
                return render_template("form_gestante.html", paciente=paciente, hoje=date.today())
            recalculate_gestante(paciente)
            registrar_auditoria("editar", "gestante", paciente.id, paciente.nome_paciente)
            if commit_session():
                registrar_historico_risco(
                    "gestante", paciente.id, paciente.classificacao_risco, risco_anterior,
                    detalhe_historico_gestante(paciente),
                )
                flash("Gestante atualizada com sucesso.", "success")
                return redirect(url_for("lista_gestantes"))
            flash("Não foi possível salvar: já existe um registro com este CPF.", "danger")
        return render_template("form_gestante.html", paciente=paciente, hoje=date.today())

    @app.route("/gestantes/excluir/<int:id>", methods=["POST"])
    def excluir_gestante(id):
        paciente = Gestante.query.get_or_404(id)
        registrar_auditoria("excluir", "gestante", paciente.id, paciente.nome_paciente)
        db.session.delete(paciente)
        db.session.commit()
        flash("Gestante removida.", "warning")
        return redirect(url_for("lista_gestantes"))

    @app.route("/agentes")
    def lista_agentes():
        agentes = AgenteSaude.query.order_by(AgenteSaude.ativo.desc(), AgenteSaude.nome).all()
        usos = agente_uso_map()
        editar_id = request.args.get("editar", type=int)
        editar = db.session.get(AgenteSaude, editar_id) if editar_id else None
        return render_template("agentes.html", agentes=agentes, usos=usos, editar=editar)

    @app.route("/agentes/novo", methods=["POST"])
    def novo_agente():
        nome = (request.form.get("nome") or "").strip()
        if not nome:
            flash("Informe o nome do agente.", "warning")
            return redirect(url_for("lista_agentes"))
        if AgenteSaude.query.filter(db.func.lower(AgenteSaude.nome) == nome.lower()).first():
            flash(f"O agente “{nome}” já está cadastrado.", "warning")
            return redirect(url_for("lista_agentes"))
        agente = AgenteSaude(
            nome=nome,
            micro_area=(request.form.get("micro_area") or "").strip() or None,
            equipe=(request.form.get("equipe") or "").strip() or None,
            ativo=True,
        )
        db.session.add(agente)
        registrar_auditoria("criar", "agente", detalhe=nome)
        if not commit_session():
            flash(f"O agente “{nome}” já está cadastrado.", "warning")
            return redirect(url_for("lista_agentes"))
        flash(f"Agente “{nome}” cadastrado com sucesso.", "success")
        return redirect(url_for("lista_agentes"))

    @app.route("/agentes/editar/<int:id>", methods=["POST"])
    def editar_agente(id):
        agente = AgenteSaude.query.get_or_404(id)
        nome = (request.form.get("nome") or "").strip()
        if not nome:
            flash("Informe o nome do agente.", "warning")
            return redirect(url_for("lista_agentes", editar=id))
        duplicado = AgenteSaude.query.filter(
            db.func.lower(AgenteSaude.nome) == nome.lower(), AgenteSaude.id != id
        ).first()
        if duplicado:
            flash(f"Já existe outro agente com o nome “{nome}”.", "warning")
            return redirect(url_for("lista_agentes", editar=id))
        nome_antigo = agente.nome
        agente.nome = nome
        agente.micro_area = (request.form.get("micro_area") or "").strip() or None
        agente.equipe = (request.form.get("equipe") or "").strip() or None
        if nome_antigo != nome:
            propagar_rename_agente(nome_antigo, nome)
        registrar_auditoria("editar", "agente", agente.id, nome)
        db.session.commit()
        flash(f"Agente “{nome}” atualizado.", "success")
        return redirect(url_for("lista_agentes"))

    @app.route("/agentes/<int:id>/status", methods=["POST"])
    def alternar_agente(id):
        agente = AgenteSaude.query.get_or_404(id)
        agente.ativo = not agente.ativo
        estado = "ativado" if agente.ativo else "desativado"
        registrar_auditoria("status", "agente", agente.id, f"{agente.nome}: {estado}")
        db.session.commit()
        flash(f"Agente “{agente.nome}” {estado}.", "success")
        return redirect(url_for("lista_agentes"))

    @app.route("/agentes/excluir/<int:id>", methods=["POST"])
    def excluir_agente(id):
        agente = AgenteSaude.query.get_or_404(id)
        nome = agente.nome
        registrar_auditoria("excluir", "agente", agente.id, nome)
        db.session.delete(agente)
        db.session.commit()
        flash(f"Agente “{nome}” removido do cadastro.", "warning")
        return redirect(url_for("lista_agentes"))

    # ---- Conta do próprio usuário ----
    @app.route("/conta/senha", methods=["GET", "POST"])
    def alterar_propria_senha():
        user = current_user()
        if request.method == "POST":
            atual = request.form.get("senha_atual") or ""
            nova = request.form.get("nova_senha") or ""
            confirma = request.form.get("confirmar_senha") or ""
            if not senha_confere(user, atual):
                flash("Senha atual incorreta.", "danger")
            elif not senha_valida(nova):
                flash(f"A nova senha deve ter ao menos {SENHA_MIN} caracteres.", "warning")
            elif nova != confirma:
                flash("A confirmação não corresponde à nova senha.", "warning")
            else:
                user.password_hash = hash_senha(nova)
                registrar_auditoria("senha", "usuario", user.id, user.username)
                db.session.commit()
                flash("Senha alterada com sucesso.", "success")
                return redirect(url_for("dashboard"))
        return render_template("conta.html")

    # ---- Administração de usuários (somente admin) ----
    @app.route("/usuarios")
    @admin_required
    def lista_usuarios():
        usuarios = Usuario.query.order_by(
            Usuario.ativo.desc(), Usuario.nome, Usuario.username
        ).all()
        return render_template(
            "usuarios.html",
            usuarios=usuarios,
            admin_master_id=admin_master_id(),
        )

    @app.route("/usuarios/novo", methods=["POST"])
    @admin_required
    def novo_usuario():
        username = (request.form.get("username") or "").strip()
        nome = (request.form.get("nome") or "").strip() or None
        papel = "admin" if request.form.get("papel") == "admin" else "padrao"
        senha = request.form.get("senha") or ""
        if not username:
            flash("Informe o nome de usuário (login).", "warning")
            return redirect(url_for("lista_usuarios"))
        if not senha_valida(senha):
            flash(f"A senha deve ter ao menos {SENHA_MIN} caracteres.", "warning")
            return redirect(url_for("lista_usuarios"))
        if Usuario.query.filter(db.func.lower(Usuario.username) == username.lower()).first():
            flash(f"O usuário “{username}” já existe.", "warning")
            return redirect(url_for("lista_usuarios"))
        db.session.add(
            Usuario(
                username=username,
                nome=nome,
                papel=papel,
                password_hash=hash_senha(senha),
                ativo=True,
            )
        )
        registrar_auditoria("criar", "usuario", detalhe=username)
        if not commit_session():
            flash(f"O usuário “{username}” já existe.", "warning")
            return redirect(url_for("lista_usuarios"))
        flash(f"Usuário “{username}” criado.", "success")
        return redirect(url_for("lista_usuarios"))

    @app.route("/usuarios/<int:id>/senha", methods=["POST"])
    @admin_required
    def resetar_senha(id):
        usuario = Usuario.query.get_or_404(id)
        nova = request.form.get("nova_senha") or ""
        if not senha_valida(nova):
            flash(f"A senha deve ter ao menos {SENHA_MIN} caracteres.", "warning")
            return redirect(url_for("lista_usuarios"))

        # A senha do admin master (criador) só é redefinida com o código de
        # recuperação do terminal — garante que apenas quem tem acesso ao
        # servidor troca a senha do dono, mesmo que outro admin veja o botão.
        if usuario.id == admin_master_id() and current_user().id != usuario.id:
            identificador = f"{request.remote_addr}|master-reset"
            if login_is_blocked(identificador):
                flash("Muitas tentativas. Aguarde alguns minutos e tente novamente.", "danger")
                return redirect(url_for("lista_usuarios"))
            codigo = (request.form.get("codigo") or "").strip()
            alvo = consumir_codigo_recuperacao(app.instance_path, codigo)
            if not alvo or alvo.lower() != usuario.username.lower():
                register_failed_login(identificador)
                flash(
                    f"Código do terminal inválido ou expirado. No servidor, rode "
                    f"“python app.py recuperar-senha {usuario.username}”.",
                    "danger",
                )
                return redirect(url_for("lista_usuarios"))
            clear_failed_logins(identificador)
            clear_codigo_recuperacao(app.instance_path)

        usuario.password_hash = hash_senha(nova)
        registrar_auditoria("reset_senha", "usuario", usuario.id, usuario.username)
        db.session.commit()
        flash(f"Senha de “{usuario.username}” redefinida.", "success")
        return redirect(url_for("lista_usuarios"))

    @app.route("/usuarios/<int:id>/papel", methods=["POST"])
    @admin_required
    def alternar_papel(id):
        usuario = Usuario.query.get_or_404(id)
        if usuario.id == admin_master_id() and usuario.papel == "admin":
            flash("O administrador master criador do sistema não pode ser rebaixado.", "warning")
            return redirect(url_for("lista_usuarios"))
        if usuario.papel == "admin" and contar_admins_ativos() <= 1:
            flash("Não é possível rebaixar o último administrador ativo.", "warning")
            return redirect(url_for("lista_usuarios"))
        usuario.papel = "padrao" if usuario.papel == "admin" else "admin"
        registrar_auditoria("papel", "usuario", usuario.id, f"{usuario.username}: {usuario.papel}")
        db.session.commit()
        flash(f"Papel de “{usuario.username}” atualizado para {usuario.papel}.", "success")
        return redirect(url_for("lista_usuarios"))

    @app.route("/usuarios/<int:id>/status", methods=["POST"])
    @admin_required
    def alternar_usuario(id):
        usuario = Usuario.query.get_or_404(id)
        if usuario.id == admin_master_id() and usuario.ativo:
            flash("O administrador master criador do sistema não pode ser desativado.", "warning")
            return redirect(url_for("lista_usuarios"))
        if usuario.ativo and usuario.papel == "admin" and contar_admins_ativos() <= 1:
            flash("Não é possível desativar o último administrador ativo.", "warning")
            return redirect(url_for("lista_usuarios"))
        usuario.ativo = not usuario.ativo
        estado = "ativado" if usuario.ativo else "desativado"
        registrar_auditoria("status", "usuario", usuario.id, f"{usuario.username}: {estado}")
        db.session.commit()
        flash(f"Usuário “{usuario.username}” {estado}.", "success")
        return redirect(url_for("lista_usuarios"))

    @app.route("/usuarios/<int:id>/excluir", methods=["POST"])
    @admin_required
    def excluir_usuario(id):
        usuario = Usuario.query.get_or_404(id)
        if usuario.id == current_user().id:
            flash("Você não pode excluir a própria conta.", "warning")
            return redirect(url_for("lista_usuarios"))
        if usuario.id == admin_master_id():
            flash("O administrador master criador do sistema não pode ser excluído.", "warning")
            return redirect(url_for("lista_usuarios"))
        if usuario.papel == "admin" and contar_admins_ativos() <= 1:
            flash("Não é possível excluir o último administrador ativo.", "warning")
            return redirect(url_for("lista_usuarios"))
        nome = usuario.username
        registrar_auditoria("excluir", "usuario", usuario.id, nome)
        db.session.delete(usuario)
        db.session.commit()
        flash(f"Usuário “{nome}” removido.", "warning")
        return redirect(url_for("lista_usuarios"))

    @app.route("/auditoria")
    @admin_required
    def auditoria():
        page = request.args.get("page", 1, type=int)
        f_usuario = request.args.get("usuario", "").strip()
        f_acao = request.args.get("acao", "").strip()
        f_entidade = request.args.get("entidade", "").strip()

        query = Auditoria.query
        if f_usuario:
            query = query.filter(Auditoria.usuario == f_usuario)
        if f_acao:
            query = query.filter(Auditoria.acao == f_acao)
        if f_entidade:
            query = query.filter(Auditoria.entidade == f_entidade)
        query = query.order_by(Auditoria.criado_em.desc(), Auditoria.id.desc())

        registros = db.paginate(query, page=page, per_page=40, error_out=False)

        usuarios = [
            row[0]
            for row in db.session.query(Auditoria.usuario)
            .distinct()
            .order_by(Auditoria.usuario)
            if row[0]
        ]
        acoes = [
            row[0]
            for row in db.session.query(Auditoria.acao).distinct().order_by(Auditoria.acao)
        ]
        entidades = [
            row[0]
            for row in db.session.query(Auditoria.entidade)
            .distinct()
            .order_by(Auditoria.entidade)
        ]
        return render_template(
            "auditoria.html",
            registros=registros,
            usuarios=usuarios,
            acoes=acoes,
            entidades=entidades,
            filtros={"usuario": f_usuario, "acao": f_acao, "entidade": f_entidade},
        )

    @app.route("/backup", methods=["POST"])
    @admin_required
    def backup_banco():
        database_path = Path(db.engine.url.database)
        if not database_path.is_absolute():
            database_path = Path(app.instance_path) / database_path
        if not database_path.exists():
            flash("Banco de dados não encontrado para backup.", "warning")
            return redirect(url_for("dashboard"))

        backup_dir = BASE_DIR / "backups"
        backup_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"estratificacao_risco_{timestamp}.db"
        # API de backup do SQLite: gera copia consistente mesmo com WAL ativo
        # (copiar o arquivo cru poderia perder transacoes ainda no -wal).
        source = sqlite3.connect(str(database_path))
        try:
            destino = sqlite3.connect(str(backup_path))
            try:
                with destino:
                    source.backup(destino)
            finally:
                destino.close()
        finally:
            source.close()
        registrar_auditoria("backup", "sistema", detalhe=backup_path.name)
        db.session.commit()
        return send_file(backup_path, as_attachment=True, download_name=backup_path.name)

    @app.route("/relatorios")
    def relatorios():
        filters = report_filters()
        rows = get_report_rows(**filters)
        page = request.args.get("page", 1, type=int)
        paginacao = paginate_list(rows, page, PER_PAGE)
        return render_template(
            "relatorios.html",
            rows=paginacao.items,
            paginacao=paginacao,
            total=len(rows),
            filters=filters,
            riscos=all_risk_labels(),
        )

    @app.route("/relatorios/exportar/excel")
    @admin_required
    def exportar_excel():
        rows = get_report_rows(**report_filters())
        output = write_excel_report(rows)
        filename = f"relatorio_estratificacao_{date.today().isoformat()}.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @app.route("/relatorios/exportar/pdf")
    @admin_required
    def exportar_pdf():
        rows = get_report_rows(**report_filters())
        output = write_pdf_report(rows)
        filename = f"relatorio_estratificacao_{date.today().isoformat()}.pdf"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype="application/pdf",
        )


app = create_app()


def _cli_recuperar_senha(argv):
    """`python app.py recuperar-senha <usuario>` — emite um código de recuperação.

    Uso restrito a quem tem acesso ao servidor (o admin). O código é exibido só
    aqui, no terminal, e depois informado na tela de login (“Esqueci minha senha”).
    """
    if not argv:
        print("Uso: python app.py recuperar-senha <usuario>")
        return 2
    username = argv[0].strip()
    with app.app_context():
        user = Usuario.query.filter(
            db.func.lower(Usuario.username) == username.lower()
        ).first()
        if not user:
            print(f"Usuário '{username}' não encontrado. Confira o nome de login.")
            return 1
        codigo, ttl = gerar_codigo_recuperacao(app.instance_path, user.username)

    print("")
    print("============================================================")
    print("  RECUPERACAO DE SENHA")
    print(f"  Usuario:               {user.username}")
    print(f"  Codigo de recuperacao: {codigo}   (valido por {ttl} min)")
    print("  Acesse http://<endereco-do-servidor>:5000/login , clique em")
    print("  \"Esqueci minha senha\" e informe este codigo com a nova senha.")
    print("============================================================")
    print("")
    return 0


if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    if argv and argv[0] == "recuperar-senha":
        sys.exit(_cli_recuperar_senha(argv[1:]))
    app.run(host="127.0.0.1", port=5000, debug=False)
