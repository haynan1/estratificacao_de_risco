"""Motor clínico da estratificação de risco.

Funções puras (sem Flask, sem banco): recebem instâncias dos modelos ou valores
primitivos e devolvem/atribuem resultados. Isso mantém a lógica clínica isolada,
testável e auditável, separada do transporte HTTP.
"""

from datetime import date, timedelta
import math
import re


PREVENT_STATUS = "Calcular Prevent"
PREVENT_STATUS_LEGACY = "Calcular PREVENT"
PREVENT_STATUS_VALUES = (PREVENT_STATUS, PREVENT_STATUS_LEGACY)
RISCO_EXTREMO = "Risco Extremo"
RISCO_MUITO_ALTO = "Risco Muito Alto"
RISCO_ALTO = "Risco Alto"
RISCO_MEDIO = "Risco Médio"
RISCO_MODERADO = "Risco Moderado"
RISCO_INTERMEDIARIO = "Risco Intermediário"
RISCO_BAIXO = "Risco Baixo"
RISCO_SEM_ADICIONAL = "Sem Risco Adicional"
# Pendência: falta a faixa do Escore de Risco Cardiovascular (calculadora estadual).
ERCV_PENDENTE = "Calcular ERCV"
IDOSO_BAIXO = "Baixo risco de vulnerabilidade clínico-funcional"
IDOSO_MODERADO = "Moderado risco de vulnerabilidade clínico-funcional"
IDOSO_ALTO = "Alto risco de vulnerabilidade clínico-funcional"
IDOSO_ROBUSTO = "Idoso robusto"
IDOSO_RISCO_FRAGILIZACAO = "Idoso em risco de fragilização"
IDOSO_FRAGIL = "Idoso frágil"


def is_prevent_status(value):
    return (value or "").lower() == "calcular prevent"


def display_risk(value):
    return "" if is_prevent_status(value) else (value or "")


def calculate_age(birth_date, reference_date=None):
    if not birth_date:
        return None
    reference_date = reference_date or date.today()
    age = reference_date.year - birth_date.year
    if (reference_date.month, reference_date.day) < (birth_date.month, birth_date.day):
        age -= 1
    return max(age, 0)


def clamp(value, min_value, max_value):
    if value in ("", None):
        return None
    return max(min_value, min(max_value, value))


def subtract_months(value, months):
    month = value.month - months
    year = value.year
    while month <= 0:
        month += 12
        year -= 1
    days_in_month = [
        31,
        29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ][month - 1]
    return date(year, month, min(value.day, days_in_month))


def extract_pas(pa_text):
    match = re.search(r"\d+", pa_text or "")
    return int(match.group(0)) if match else None


def extract_pad(pa_text):
    """Segunda medida da PA (diastólica), ex.: '140/90' -> 90."""
    numeros = re.findall(r"\d+", pa_text or "")
    return int(numeros[1]) if len(numeros) >= 2 else None


def parse_ig_days(value):
    if not value:
        return None
    text = value.lower().replace(" ", "")
    match = re.search(r"(\d+)s(?:(?:\+)?(\d+)d?)?", text)
    if not match:
        return None
    weeks = int(match.group(1))
    days = int(match.group(2) or 0)
    return weeks * 7 + days


def format_ig(total_days):
    if total_days is None or total_days < 0:
        return ""
    return f"{total_days // 7}s + {total_days % 7}d"


def parse_percent_or_float(value):
    if value in ("", None):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    try:
        if text.endswith("%"):
            return float(text[:-1]) / 100
        return float(text)
    except ValueError:
        return None


def calculate_tfg_ckd_epi(creatinina, idade, sexo):
    if not creatinina or creatinina <= 0 or not idade:
        return None
    feminino = (sexo or "").lower().startswith("f")
    kappa = 0.7 if feminino else 0.9
    alpha = -0.241 if feminino else -0.302
    ratio = creatinina / kappa
    result = 142 * (min(ratio, 1) ** alpha) * (max(ratio, 1) ** -1.2) * (0.9938**idade)
    if feminino:
        result *= 1.012
    return result


def calculate_prevent_log_odds(avaliacao, sexo):
    idade = avaliacao.idade_cal_prevent
    ct = avaliacao.ct_calc
    hdl = avaliacao.hdl_calc_prevent
    pas = avaliacao.pas_calc_prevent
    tfg = avaliacao.tfg_cal_prevent
    if None in (idade, ct, hdl, pas, tfg):
        return None

    age = (idade - 55) / 10
    non_hdl = ((ct - hdl) * 0.02586) - 3.5
    hdl_term = ((hdl * 0.02586) - 1.3) / 0.3
    sbp_low = (min(pas, 110) - 110) / 20
    sbp_high = (max(pas, 110) - 130) / 20
    egfr_low = (min(tfg, 60) - 60) / -15
    egfr_high = (max(tfg, 60) - 90) / -15
    dm2 = 1 if avaliacao.dm2 else 0
    fumante = 1 if avaliacao.fumante else 0
    anti_hipertensivo = 1 if avaliacao.anti_hipertensivo else 0
    estatina = 1 if avaliacao.uso_estatina else 0
    feminino = (sexo or "").lower().startswith("f")

    if feminino:
        return (
            -3.307728
            + 0.7939329 * age
            + 0.0305239 * non_hdl
            - 0.1606857 * hdl_term
            - 0.2394003 * sbp_low
            + 0.360078 * sbp_high
            + 0.8667604 * dm2
            + 0.5360739 * fumante
            + 0.6045917 * egfr_low
            + 0.0433769 * egfr_high
            + 0.3151672 * anti_hipertensivo
            - 0.1477655 * estatina
            - 0.0663612 * anti_hipertensivo * sbp_high
            + 0.1197879 * estatina * non_hdl
            - 0.0819715 * age * non_hdl
            + 0.0306769 * age * hdl_term
            - 0.0946348 * age * sbp_high
            - 0.27057 * age * dm2
            - 0.078715 * age * fumante
            - 0.1637806 * age * egfr_low
        )

    return (
        -3.031168
        + 0.7688528 * age
        + 0.0736174 * non_hdl
        - 0.0954431 * hdl_term
        - 0.4347345 * sbp_low
        + 0.3362658 * sbp_high
        + 0.7692857 * dm2
        + 0.4386871 * fumante
        + 0.5378979 * egfr_low
        + 0.0164827 * egfr_high
        + 0.288879 * anti_hipertensivo
        - 0.1337349 * estatina
        - 0.0475924 * anti_hipertensivo * sbp_high
        + 0.150273 * estatina * non_hdl
        - 0.0517874 * age * non_hdl
        + 0.0191169 * age * hdl_term
        - 0.1049477 * age * sbp_high
        - 0.2251948 * age * dm2
        - 0.0895067 * age * fumante
        - 0.1543702 * age * egfr_low
    )


def sync_prevent_from_patient(avaliacao, paciente):
    avaliacao.idade_cal_prevent = clamp(paciente.idade, 30, 79)
    avaliacao.dm2 = bool(paciente.dm2)
    if avaliacao.pas is None:
        avaliacao.pas = paciente.pas
    avaliacao.pas_calc_prevent = clamp(avaliacao.pas, 90, 200)
    avaliacao.ct_calc = clamp(avaliacao.ct, 130, 320)
    avaliacao.hdl_calc_prevent = clamp(avaliacao.hdl, 20, 100)
    avaliacao.tfg_cal_prevent = clamp(avaliacao.tfg_ckd_epi, 15, 140)


def calculate_prevent(avaliacao, paciente):
    sync_prevent_from_patient(avaliacao, paciente)
    avaliacao.tfg_ckd_epi = calculate_tfg_ckd_epi(
        avaliacao.cr, paciente.idade, paciente.sexo
    )
    avaliacao.tfg_cal_prevent = clamp(avaliacao.tfg_ckd_epi, 15, 140)
    avaliacao.log_odds = calculate_prevent_log_odds(avaliacao, paciente.sexo)
    for attr in ("ct_calc", "hdl_calc_prevent", "tfg_ckd_epi", "tfg_cal_prevent", "log_odds"):
        value = getattr(avaliacao, attr)
        if value is not None:
            setattr(avaliacao, attr, round(value, 4))

    if paciente.risco_estratificado in (RISCO_EXTREMO, RISCO_MUITO_ALTO):
        avaliacao.risco_cardiovascular_10_anos = "Não aplicável"
    elif not all([avaliacao.ct, avaliacao.hdl, avaliacao.pas, avaliacao.cr]):
        avaliacao.risco_cardiovascular_10_anos = "Inserir exames"
    elif avaliacao.log_odds is not None:
        risk = math.exp(avaliacao.log_odds) / (1 + math.exp(avaliacao.log_odds))
        avaliacao.risco_cardiovascular_10_anos = f"{risk:.1%}"


def prevent_risk_value(paciente):
    if not paciente.avaliacao_prevent:
        return None
    return parse_percent_or_float(paciente.avaliacao_prevent.risco_cardiovascular_10_anos)


# ----------------------------------------------------------------------------
# Escore de Risco Global (ERG) — Framingham revisado (D'Agostino 2008)
# ----------------------------------------------------------------------------
# Escore que a calculadora estadual (calculadora-risco.saude.go.gov.br) usa e que
# a Nota Técnica de DM (Res. 1193/2025) adota para a faixa do ERCV. Coeficientes
# oficiais do modelo General CVD 10 anos (D'Agostino RB et al., Circulation 2008;
# 117:743-753; Framingham Heart Study). Risco de evento CV (coronário, cerebro-
# vascular, DAP, IC) em 10 anos.
# Versão em PONTOS (oficial) do ERG — a mesma da calculadora estadual, conforme a
# Nota Técnica de Risco Cardiovascular (SES-GO; Tabelas 1 e 2). Cada fator recebe
# pontos por faixa; a soma mapeia para a estimativa de risco em 10 anos. Cada
# bracket é (limite_superior_inclusive, pontos); o último cobre o restante.
_ERG_PONTOS = {
    "M": {
        "idade": [(34, 0), (39, 2), (44, 5), (49, 6), (54, 8), (59, 10),
                  (64, 11), (69, 12), (74, 14), (float("inf"), 15)],
        "hdl": [(34, 2), (44, 1), (49, 0), (59, -1), (float("inf"), -2)],
        "ct": [(159, 0), (199, 1), (239, 2), (279, 3), (float("inf"), 4)],
        "pas_nt": [(119, -2), (129, 0), (139, 1), (159, 2), (float("inf"), 3)],
        "pas_t": [(119, 0), (129, 2), (139, 3), (159, 4), (float("inf"), 5)],
        "fumante": 4, "diabetes": 3, "pts_min": -3, "pts_max": 18,
        "risco": {-3: 0.009, -2: 0.011, -1: 0.014, 0: 0.016, 1: 0.019, 2: 0.023,
                  3: 0.028, 4: 0.033, 5: 0.039, 6: 0.047, 7: 0.056, 8: 0.067,
                  9: 0.079, 10: 0.094, 11: 0.112, 12: 0.132, 13: 0.156, 14: 0.184,
                  15: 0.216, 16: 0.253, 17: 0.294, 18: 0.305},
    },
    "F": {
        "idade": [(34, 0), (39, 2), (44, 4), (49, 5), (54, 7), (59, 8),
                  (64, 9), (69, 10), (74, 11), (float("inf"), 12)],
        "hdl": [(34, 2), (44, 1), (49, 0), (59, -1), (float("inf"), -2)],
        "ct": [(159, 0), (199, 1), (239, 3), (279, 4), (float("inf"), 5)],
        "pas_nt": [(119, -3), (129, 0), (139, 1), (149, 2), (159, 4), (float("inf"), 5)],
        "pas_t": [(119, -1), (129, 2), (139, 3), (149, 5), (159, 6), (float("inf"), 7)],
        "fumante": 3, "diabetes": 4, "pts_min": -2, "pts_max": 21,
        "risco": {-2: 0.009, -1: 0.010, 0: 0.012, 1: 0.015, 2: 0.017, 3: 0.020,
                  4: 0.024, 5: 0.028, 6: 0.033, 7: 0.039, 8: 0.045, 9: 0.053,
                  10: 0.063, 11: 0.073, 12: 0.086, 13: 0.100, 14: 0.117, 15: 0.137,
                  16: 0.159, 17: 0.185, 18: 0.216, 19: 0.248, 20: 0.285, 21: 0.305},
    },
}


def _pontos_bracket(valor, brackets):
    for limite, pontos in brackets:
        if valor <= limite:
            return pontos
    return brackets[-1][1]


def calcular_erg(sexo, idade, colesterol_total, hdl, pas, tratado, fumante, diabetes):
    """Risco cardiovascular global em 10 anos (fração 0–1) pelo ERG em PONTOS.

    Versão oficial da calculadora estadual (NT de Risco Cardiovascular SES-GO,
    Tabelas 1 e 2). Unidades: idade em anos; colesterol total e HDL em mg/dL;
    PAS em mmHg. `tratado` = em uso de anti-hipertensivo. None se faltar dado.
    """
    if not all(isinstance(v, (int, float)) and v > 0 for v in (idade, colesterol_total, hdl, pas)):
        return None
    t = _ERG_PONTOS["F"] if (sexo or "").lower().startswith("f") else _ERG_PONTOS["M"]
    total = (
        _pontos_bracket(idade, t["idade"])
        + _pontos_bracket(hdl, t["hdl"])
        + _pontos_bracket(colesterol_total, t["ct"])
        + _pontos_bracket(pas, t["pas_t"] if tratado else t["pas_nt"])
        + (t["fumante"] if fumante else 0)
        + (t["diabetes"] if diabetes else 0)
    )
    total = max(t["pts_min"], min(t["pts_max"], total))
    return t["risco"][total]


def faixa_erg(risco, sexo):
    """Faixa do ERCV conforme cortes sexo-específicos da NT (Res. 1193/2025):
    baixo <5%; intermediário 5–<20% (H) / 5–<10% (M); alto ≥20% (H) / ≥10% (M).
    """
    if risco is None:
        return None
    pct = risco * 100
    limite_alto = 10 if (sexo or "").lower().startswith("f") else 20
    if pct < 5:
        return "baixo"
    if pct < limite_alto:
        return "intermediario"
    return "alto"


# -----------------------------------------------------------------------------
# FINDRISC — risco de desenvolver diabetes tipo 2 em 10 anos (Escore Finlandês)
# -----------------------------------------------------------------------------
# Instrumento em pontos (SBD 2024; Lindström & Tuomilehto 2003). Pontuação 0–26.
def calcular_findrisc(idade, imc, cintura, sexo, atividade_fisica,
                      come_vegetais_diario, medicamento_pressao, glicemia_alta,
                      familiar_diabetes):
    """Pontuação FINDRISC (0–26) ou None se faltar idade, IMC ou cintura.

    `familiar_diabetes`: "nao" | "segundo_grau" (avós/tios/primos → 3) |
    "primeiro_grau" (pais/irmãos/filhos → 5).
    """
    if not all(isinstance(v, (int, float)) and v > 0 for v in (idade, imc, cintura)):
        return None
    pontos = 0
    # 1. Idade
    if idade > 64:
        pontos += 4
    elif idade >= 55:
        pontos += 3
    elif idade >= 45:
        pontos += 2
    # 2. IMC
    if imc > 30:
        pontos += 3
    elif imc >= 25:
        pontos += 1
    # 3. Circunferência da cintura (sexo-específico)
    if (sexo or "").lower().startswith("f"):
        pontos += 4 if cintura > 88 else (3 if cintura >= 80 else 0)
    else:
        pontos += 4 if cintura > 102 else (3 if cintura >= 94 else 0)
    # 4. Atividade física < 30 min/dia
    if not atividade_fisica:
        pontos += 2
    # 5. Não come vegetais/frutas todos os dias
    if not come_vegetais_diario:
        pontos += 1
    # 6. Uso de medicamento para pressão alta
    if medicamento_pressao:
        pontos += 2
    # 7. Glicose alta detectada alguma vez
    if glicemia_alta:
        pontos += 5
    # 8. Familiar com diabetes
    fam = (familiar_diabetes or "").lower()
    if fam == "primeiro_grau":
        pontos += 5
    elif fam == "segundo_grau":
        pontos += 3
    return pontos


def faixa_findrisc(pontos):
    """Faixa do FINDRISC: baixo <7; levemente elevado 7–11; moderado 12–14;
    alto 15–20; muito alto >20."""
    if pontos is None:
        return None
    if pontos < 7:
        return "baixo"
    if pontos <= 11:
        return "levemente_elevado"
    if pontos <= 14:
        return "moderado"
    if pontos <= 20:
        return "alto"
    return "muito_alto"


def update_exames_cardiovasc(paciente):
    avaliacao = paciente.avaliacao_prevent
    if not avaliacao or not avaliacao.ct:
        paciente.exames_cardiovasc = "Solicitar"
    elif avaliacao.data_exames and avaliacao.data_exames < subtract_months(date.today(), 6):
        paciente.exames_cardiovasc = "Exame há + 6m"
    else:
        paciente.exames_cardiovasc = "Adequado"


def _flag(obj, name, default=False):
    """Lê booleano tolerando None (colunas novas em bancos migrados = NULL)."""
    valor = getattr(obj, name, None)
    return default if valor is None else bool(valor)


def _pa_estagio(pas, pad):
    """Estágio da PA pela maior entre sistólica e diastólica (NT 11/2021, Quadro 1).

    Retorna 3, 2, 1 (estágios 1–3), 0 (pré-hipertensão) ou -1 (abaixo de pré-HAS).
    """
    pas = pas or 0
    pad = pad or 0
    if pas >= 180 or pad >= 110:
        return 3
    if pas >= 160 or pad >= 100:
        return 2
    if pas >= 140 or pad >= 90:
        return 1
    if pas >= 130 or pad >= 85:
        return 0
    return -1


def _fatores_risco_has(paciente):
    """Contagem de fatores de risco CV da Diretriz HAS 2020 (NT 11/2021, Quadro 4)."""
    idade = paciente.idade or 0
    sexo = (paciente.sexo or "").lower()
    masculino = sexo.startswith("m")
    feminino = sexo.startswith("f")
    fatores = 0
    if masculino:
        fatores += 1
    if (masculino and idade > 55) or (feminino and idade > 65):
        fatores += 1
    for attr in ("tabagismo", "dcv_familiar_precoce", "dislipidemia", "obesidade"):
        if _flag(paciente, attr):
            fatores += 1
    if _flag(paciente, "dm2") or _flag(paciente, "dm1"):
        fatores += 1
    return fatores


def _tem_loa_dcv_drc_dm(paciente):
    return (
        _flag(paciente, "loa")
        or _flag(paciente, "dcv_at_sintomatica")
        or _flag(paciente, "doenca_aterosclerotica")
        or _flag(paciente, "drc")
        or _flag(paciente, "dm2")
        or _flag(paciente, "dm1")
    )


def classificar_has(paciente):
    """Risco do hipertenso sem diabetes (NT 11/2021, Quadro 3)."""
    if _tem_loa_dcv_drc_dm(paciente):
        return RISCO_ALTO
    estagio = _pa_estagio(paciente.pas, getattr(paciente, "pad", None))
    if estagio < 0:
        return RISCO_BAIXO
    fatores = _fatores_risco_has(paciente)
    coluna = 0 if fatores == 0 else (1 if fatores <= 2 else 2)
    matriz = {
        0: (RISCO_SEM_ADICIONAL, RISCO_BAIXO, RISCO_MODERADO),
        1: (RISCO_BAIXO, RISCO_MODERADO, RISCO_ALTO),
        2: (RISCO_MODERADO, RISCO_ALTO, RISCO_ALTO),
        3: (RISCO_ALTO, RISCO_ALTO, RISCO_ALTO),
    }
    return matriz[estagio][coluna]


def classificar_dm(paciente):
    """Cascata de risco do DM / pré-diabetes (Res. 1193/2025, Quadro 9).

    Devolve None quando o paciente não tem DM nem pré-diabetes (segue para HAS).
    """
    dm1 = _flag(paciente, "dm1")
    tem_dm = _flag(paciente, "dm2") or dm1
    hba1c = paciente.ultima_hba1c
    ercv = (getattr(paciente, "ercv_faixa", "") or "").strip().lower()

    if tem_dm:
        aterosclerose = _flag(paciente, "doenca_aterosclerotica") or _flag(paciente, "dcv_at_sintomatica")
        internacao = _flag(paciente, "internacao_aguda_12m")
        hba1c_descontrole = hba1c is not None and hba1c > 7.5
        # MUITO ALTO
        if internacao or aterosclerose or (dm1 and hba1c_descontrole):
            return RISCO_MUITO_ALTO
        # DM1 sem descontrole grave é sempre pelo menos ALTO (nunca médio/baixo).
        if dm1:
            return RISCO_ALTO
        # DM2:
        pressao_ok = _flag(paciente, "controle_pressorico_adequado", default=True)
        complicacao = _flag(paciente, "complicacao_cronica")
        if hba1c_descontrole or not pressao_ok or complicacao or ercv == "alto":
            return RISCO_ALTO
        # DM2 controlado: sem a faixa do ERCV não dá para descartar ERCV alto.
        if hba1c is None or not ercv:
            return ERCV_PENDENTE
        return RISCO_MEDIO

    if _flag(paciente, "pre_diabetes"):
        if not _flag(paciente, "autocuidado_suficiente", default=True):
            return RISCO_MEDIO
        if not ercv:
            return ERCV_PENDENTE
        return RISCO_BAIXO if ercv == "baixo" else RISCO_MEDIO

    return None


def calculate_cronico_risk(paciente):
    """Estratificação de risco cardiovascular conforme notas técnicas SES-GO.

    DM/pré-diabetes: Res. CIB 1193/2025 (Quadro 9). Hipertensão sem DM: NT 11/2021
    (Quadro 3). A faixa do ERCV (Framingham revisado) é capturada da calculadora
    estadual, não recalculada aqui.

    ATENÇÃO: lógica clínica. Deve passar por validação profissional antes do uso
    assistencial. É apoio à decisão, não substitui o julgamento clínico.
    """
    resultado_dm = classificar_dm(paciente)
    if resultado_dm is not None:
        return resultado_dm
    if _flag(paciente, "has"):
        return classificar_has(paciente)
    return RISCO_BAIXO


def recalculate_cronico(paciente):
    paciente.idade = calculate_age(paciente.data_nascimento)
    paciente.pas = extract_pas(paciente.ultima_pa)
    paciente.pad = extract_pad(paciente.ultima_pa)
    if paciente.avaliacao_prevent:
        calculate_prevent(paciente.avaliacao_prevent, paciente)
    paciente.risco_estratificado = calculate_cronico_risk(paciente)
    update_exames_cardiovasc(paciente)


def calculate_imc(peso, estatura):
    if peso and estatura:
        altura_m = estatura / 100
        if altura_m > 0:
            return round(peso / (altura_m**2), 2)
    return None


def calcular_ivcf20(paciente):
    """Pontua o IVCF-20 conforme a Nota Técnica de Saúde da Pessoa Idosa.

    Pontuação: idade 0/1/3; autopercepção 1; AIVD máximo 4; banho 6;
    cognição 1+1+2; humor 2+2; mobilidade/comunicação/comorbidades conforme
    instrumento, total máximo 40.
    """
    pontos = 0
    idade = getattr(paciente, "idade", None)
    if idade is not None:
        if idade >= 85:
            pontos += 3
        elif idade >= 75:
            pontos += 1

    if _flag(paciente, "ivcf_autopercepcao_ruim"):
        pontos += 1

    if any(
        _flag(paciente, attr)
        for attr in ("ivcf_compras", "ivcf_dinheiro", "ivcf_domestico")
    ):
        pontos += 4

    pesos = (
        ("ivcf_banho", 6),
        ("ivcf_esquecimento", 1),
        ("ivcf_esquecimento_piorando", 1),
        ("ivcf_esquecimento_impede", 2),
        ("ivcf_desanimo", 2),
        ("ivcf_perda_interesse", 2),
        ("ivcf_bracos", 1),
        ("ivcf_objetos", 1),
        ("ivcf_capacidade_aerobica", 2),
        ("ivcf_marcha", 2),
        ("ivcf_quedas", 2),
        ("ivcf_incontinencia", 2),
        ("ivcf_visao", 2),
        ("ivcf_audicao", 2),
        ("ivcf_comorbidades", 4),
    )
    for attr, peso in pesos:
        if _flag(paciente, attr):
            pontos += peso
    return pontos


def classificar_ivcf20(pontos):
    if pontos is None:
        return ""
    if pontos <= 6:
        return IDOSO_BAIXO
    if pontos <= 14:
        return IDOSO_MODERADO
    return IDOSO_ALTO


def estrato_idoso_por_ivcf(pontos):
    if pontos is None:
        return ""
    if pontos <= 6:
        return IDOSO_ROBUSTO
    if pontos <= 14:
        return IDOSO_RISCO_FRAGILIZACAO
    return IDOSO_FRAGIL


def recalculate_idoso(paciente):
    paciente.idade = calculate_age(paciente.data_nascimento)
    paciente.ivcf_pontos = calcular_ivcf20(paciente)
    paciente.classificacao_ivcf = classificar_ivcf20(paciente.ivcf_pontos)
    paciente.estrato_clinico_funcional = estrato_idoso_por_ivcf(paciente.ivcf_pontos)


def recalculate_gestante(paciente):
    paciente.idade = calculate_age(paciente.data_nascimento)
    paciente.imc = calculate_imc(paciente.peso, paciente.estatura)

    gestante = (paciente.grupo or "").strip().lower() != "puérpera"
    if gestante:
        ig_base_dias = parse_ig_days(paciente.ig_primeiro_usg)
        if paciente.primeiro_usg and ig_base_dias is not None:
            total_dias = (date.today() - paciente.primeiro_usg).days + ig_base_dias
        elif paciente.dum:
            total_dias = (date.today() - paciente.dum).days
        else:
            total_dias = None
        paciente.ig_atual_semanas = format_ig(total_dias)
        paciente.ig_semanas = total_dias // 7 if total_dias is not None and total_dias >= 0 else None
        paciente.dpp = (
            date.today() - timedelta(days=total_dias) + timedelta(days=280)
            if total_dias is not None and total_dias >= 0
            else None
        )
    else:
        paciente.ig_atual_semanas = ""

    # Estratificação conforme NT 6/2024 SES-GO (Quadro 2):
    #  - Alto risco: obesidade IMC >= 40, ou HAS/DM em descontrole, ou qualquer
    #    critério clínico de alto risco marcado pelo profissional.
    #  - Intermediário: idade < 15 ou > 40 anos; baixo peso (IMC < 18,5) ou
    #    obesidade sem comorbidade (IMC 30–39,9); ou critério intermediário marcado.
    #  - Habitual: 15 a 40 anos e IMC 18,5–29,9, sem fatores.
    imc = paciente.imc
    if (
        paciente.criterio_alto_risco
        or paciente.hac_descontrole
        or paciente.dm_descontrole
        or (imc is not None and imc >= 40)
    ):
        paciente.classificacao_risco = "ALTO RISCO"
    elif (
        paciente.criterio_risco_intermediario
        or (paciente.idade is not None and (paciente.idade < 15 or paciente.idade > 40))
        or (imc is not None and (imc < 18.5 or imc >= 30))
    ):
        paciente.classificacao_risco = "RISCO INTERMEDIÁRIO"
    elif paciente.nome_paciente:
        paciente.classificacao_risco = "RISCO HABITUAL"
