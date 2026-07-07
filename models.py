from datetime import datetime

from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


class Auditoria(db.Model):
    __tablename__ = "auditoria"

    id = db.Column(db.Integer, primary_key=True)
    usuario = db.Column(db.String(80), index=True)
    acao = db.Column(db.String(40), nullable=False, index=True)
    entidade = db.Column(db.String(40), nullable=False, index=True)
    entidade_id = db.Column(db.Integer)
    detalhe = db.Column(db.String(200))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class HistoricoRisco(db.Model):
    """Linha do tempo da estratificação de cada paciente (evolução clínica).

    Um registro é criado sempre que o risco muda ao salvar o paciente. Serve para
    acompanhar melhora ou piora — em especial, quando o risco é reduzido. Vale
    para os três módulos (crônico, gestante, idoso) via `tipo` + `paciente_id`.
    """

    __tablename__ = "historico_risco"

    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False, index=True)
    paciente_id = db.Column(db.Integer, nullable=False, index=True)
    risco = db.Column(db.String(80), nullable=False)
    risco_anterior = db.Column(db.String(80))
    # "inicial" | "desceu" | "subiu" | "manteve" | "atualizado"
    tendencia = db.Column(db.String(12), nullable=False, default="inicial")
    detalhe = db.Column(db.String(200))
    autor = db.Column(db.String(80))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class AgenteSaude(db.Model):
    __tablename__ = "agente_saude"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), unique=True, nullable=False, index=True)
    micro_area = db.Column(db.String(40))
    equipe = db.Column(db.String(80))
    ativo = db.Column(db.Boolean, default=True, nullable=False)


class Usuario(db.Model):
    __tablename__ = "usuario"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    nome = db.Column(db.String(120))
    papel = db.Column(db.String(20), default="padrao", nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.now)

    @property
    def is_admin(self):
        return self.papel == "admin"


class PacienteCronico(db.Model):
    __tablename__ = "paciente_cronico"

    id = db.Column(db.Integer, primary_key=True)
    nome_completo = db.Column(db.String(160), nullable=False)
    acs = db.Column(db.String(120))
    data_nascimento = db.Column(db.Date)
    idade = db.Column(db.Integer)
    sexo = db.Column(db.String(30))
    cpf = db.Column(db.String(14), unique=True, nullable=False, index=True)
    has = db.Column(db.Boolean, default=False, nullable=False)
    dm2 = db.Column(db.Boolean, default=False, nullable=False)
    dcv_at_sintomatica = db.Column(db.Boolean, default=False, nullable=False)
    condicoes_alto_risco = db.Column(db.Integer, default=0, nullable=False)
    loa = db.Column(db.Boolean, default=False, nullable=False)
    ateroesclerose_subclinica = db.Column(db.Boolean, default=False, nullable=False)
    num_eventos_previos = db.Column(db.Integer, default=0, nullable=False)
    especialista = db.Column(db.String(120))
    emulti = db.Column(db.Boolean, default=False, nullable=False)
    risco_estratificado = db.Column(db.String(80))
    exames_cardiovasc = db.Column(db.String(200))
    ultima_hba1c = db.Column(db.Float)
    data_ult_hba1c = db.Column(db.Date)
    ultima_pa = db.Column(db.String(20))
    pas = db.Column(db.Integer)
    pad = db.Column(db.Integer)
    data_ult_pa = db.Column(db.Date)

    # Estratificação conforme notas técnicas SES-GO (DM: Res. 1193/2025; HAS: NT 11/2021).
    pre_diabetes = db.Column(db.Boolean, default=False, nullable=False)
    dm1 = db.Column(db.Boolean, default=False, nullable=False)
    autocuidado_suficiente = db.Column(db.Boolean, default=True, nullable=False)
    controle_pressorico_adequado = db.Column(db.Boolean, default=True, nullable=False)
    complicacao_cronica = db.Column(db.Boolean, default=False, nullable=False)
    internacao_aguda_12m = db.Column(db.Boolean, default=False, nullable=False)
    doenca_aterosclerotica = db.Column(db.Boolean, default=False, nullable=False)
    drc = db.Column(db.Boolean, default=False, nullable=False)
    tabagismo = db.Column(db.Boolean, default=False, nullable=False)
    dislipidemia = db.Column(db.Boolean, default=False, nullable=False)
    dcv_familiar_precoce = db.Column(db.Boolean, default=False, nullable=False)
    obesidade = db.Column(db.Boolean, default=False, nullable=False)
    # Faixa do Escore de Risco Cardiovascular (calculadora estadual): baixo/intermediario/alto.
    ercv_faixa = db.Column(db.String(20))
    # Percentual do ERG (Framingham revisado) em 10 anos, quando calculado no sistema.
    ercv_percentual = db.Column(db.Float)
    # Base do último cálculo: "exames" (colesterol/HDL) ou "imc" (peso/altura).
    ercv_base = db.Column(db.String(10))
    # Antropometria — opcional. Só usada quando a base do cálculo é IMC.
    peso = db.Column(db.Float)
    estatura = db.Column(db.Float)

    avaliacao_prevent = db.relationship(
        "AvaliacaoPrevent",
        back_populates="paciente",
        uselist=False,
        cascade="all, delete-orphan",
    )

    @property
    def precisa_prevent(self):
        return not self.prevent_calculado and not self.prevent_nao_aplicavel

    @property
    def prevent_calculado(self):
        if not self.avaliacao_prevent:
            return False
        resultado = (self.avaliacao_prevent.risco_cardiovascular_10_anos or "").strip()
        return resultado.endswith("%")

    @property
    def prevent_nao_aplicavel(self):
        if not self.avaliacao_prevent:
            return False
        resultado = (self.avaliacao_prevent.risco_cardiovascular_10_anos or "").strip()
        return resultado.lower() == "não aplicável"


class PacienteIdoso(db.Model):
    __tablename__ = "paciente_idoso"

    id = db.Column(db.Integer, primary_key=True)
    nome_completo = db.Column(db.String(160), nullable=False)
    cpf = db.Column(db.String(14), unique=True, nullable=False, index=True)
    acs = db.Column(db.String(120))
    data_nascimento = db.Column(db.Date)
    idade = db.Column(db.Integer)
    sexo = db.Column(db.String(30))
    telefone = db.Column(db.String(40))

    ivcf_autopercepcao_ruim = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_compras = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_dinheiro = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_domestico = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_banho = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_esquecimento = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_esquecimento_piorando = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_esquecimento_impede = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_desanimo = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_perda_interesse = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_bracos = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_objetos = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_capacidade_aerobica = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_marcha = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_quedas = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_incontinencia = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_visao = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_audicao = db.Column(db.Boolean, default=False, nullable=False)
    ivcf_comorbidades = db.Column(db.Boolean, default=False, nullable=False)

    ivcf_pontos = db.Column(db.Integer, default=0, nullable=False)
    classificacao_ivcf = db.Column(db.String(120))
    estrato_clinico_funcional = db.Column(db.String(80))
    observacoes = db.Column(db.Text)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class AvaliacaoPrevent(db.Model):
    __tablename__ = "avaliacao_prevent"

    id = db.Column(db.Integer, primary_key=True)
    paciente_id = db.Column(
        db.Integer, db.ForeignKey("paciente_cronico.id"), unique=True, nullable=False
    )
    idade_cal_prevent = db.Column(db.Integer)
    ct_calc = db.Column(db.Float)
    data_exames = db.Column(db.Date)
    ct = db.Column(db.Float)
    hdl_calc_prevent = db.Column(db.Float)
    hdl = db.Column(db.Float)
    pas_calc_prevent = db.Column(db.Integer)
    tfg_cal_prevent = db.Column(db.Float)
    tfg_ckd_epi = db.Column(db.Float)
    cr = db.Column(db.Float)
    ldl = db.Column(db.Float)
    pas = db.Column(db.Integer)
    dm2 = db.Column(db.Boolean, default=False, nullable=False)
    fumante = db.Column(db.Boolean, default=False, nullable=False)
    anti_hipertensivo = db.Column(db.Boolean, default=False, nullable=False)
    uso_estatina = db.Column(db.Boolean, default=False, nullable=False)
    log_odds = db.Column(db.Float)
    risco_cardiovascular_10_anos = db.Column(db.String(80))

    paciente = db.relationship("PacienteCronico", back_populates="avaliacao_prevent")


class Gestante(db.Model):
    __tablename__ = "gestante"

    id = db.Column(db.Integer, primary_key=True)
    nome_paciente = db.Column(db.String(160), nullable=False)
    acs = db.Column(db.String(120))
    ultima_consulta = db.Column(db.Date)
    grupo = db.Column(db.String(30))
    cpf = db.Column(db.String(14), unique=True, nullable=False, index=True)
    data_nascimento = db.Column(db.Date)
    idade = db.Column(db.Integer)
    raca_cor = db.Column(db.String(50))
    vulnerabilidade_familiar = db.Column(db.String(120))
    ig_semanas = db.Column(db.Integer)
    consulta_regular_ubs = db.Column(db.String(80))
    criterio_risco_intermediario = db.Column(db.Boolean, default=False, nullable=False)
    criterio_alto_risco = db.Column(db.Boolean, default=False, nullable=False)
    hac_descontrole = db.Column(db.Boolean, default=False, nullable=False)
    dm_descontrole = db.Column(db.Boolean, default=False, nullable=False)
    classificacao_risco = db.Column(db.String(80))
    avaliacao_odonto = db.Column(db.String(80))
    dum = db.Column(db.Date)
    primeiro_usg = db.Column(db.Date)
    ig_primeiro_usg = db.Column(db.String(30))
    ig_atual_semanas = db.Column(db.String(30))
    dpp = db.Column(db.Date)
    numero_gestacoes = db.Column(db.Integer)
    teste_mamae_1 = db.Column(db.Boolean, default=False, nullable=False)
    teste_mamae_2 = db.Column(db.Boolean, default=False, nullable=False)
    exames_rotina_sangue = db.Column(db.Boolean, default=False, nullable=False)
    resultados = db.Column(db.Text)
    peso = db.Column(db.Float)
    estatura = db.Column(db.Float)
    imc = db.Column(db.Float)
    glicemia_capilar = db.Column(db.Float)
    respiracao = db.Column(db.Integer)
    afu = db.Column(db.Integer)
    pa = db.Column(db.String(20))
    bcf = db.Column(db.Integer)
    freq_card = db.Column(db.Integer)

# IMC, IG, DPP e classificação de risco são calculados em domain.recalculate_gestante,
# chamado nas rotas de escrita. Fonte única de verdade, sem event listeners duplicados.
