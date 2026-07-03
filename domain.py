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
RISCO_INTERMEDIARIO = "Risco Intermediário"
RISCO_BAIXO = "Risco Baixo"


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


def update_exames_cardiovasc(paciente):
    avaliacao = paciente.avaliacao_prevent
    if not avaliacao or not avaliacao.ct:
        paciente.exames_cardiovasc = "Solicitar"
    elif avaliacao.data_exames and avaliacao.data_exames < subtract_months(date.today(), 6):
        paciente.exames_cardiovasc = "Exame há + 6m"
    else:
        paciente.exames_cardiovasc = "Adequado"


def calculate_cronico_risk(paciente):
    cond = paciente.condicoes_alto_risco or 0
    eventos = paciente.num_eventos_previos or 0
    idade = paciente.idade or calculate_age(paciente.data_nascimento) or 0
    sexo = (paciente.sexo or "").lower()
    masculino = sexo.startswith("m")
    feminino = sexo.startswith("f")
    risk_prevent = prevent_risk_value(paciente)

    if eventos >= 2 or (eventos == 1 and cond >= 2):
        return RISCO_EXTREMO
    if eventos >= 1 or cond >= 3 or paciente.dcv_at_sintomatica or (paciente.dm2 and paciente.loa):
        return RISCO_MUITO_ALTO
    if (
        (paciente.dm2 and 1 <= cond <= 2 and eventos == 0 and not paciente.dcv_at_sintomatica)
        or (paciente.dm2 and cond == 0 and ((masculino and idade >= 50) or (feminino and idade >= 56)))
        or paciente.ateroesclerose_subclinica
    ):
        return RISCO_ALTO
    if not paciente.avaliacao_prevent or not all(
        [
            paciente.avaliacao_prevent.ct,
            paciente.avaliacao_prevent.hdl,
            paciente.avaliacao_prevent.pas,
            paciente.avaliacao_prevent.cr,
        ]
    ):
        return PREVENT_STATUS
    if risk_prevent is not None and (risk_prevent >= 0.2 or (0.05 <= risk_prevent < 0.2 and cond > 0)):
        return RISCO_ALTO
    if (
        paciente.dm2
        and cond == 0
        and ((masculino and idade < 50) or (feminino and idade < 56))
    ) or (risk_prevent is not None and 0.05 <= risk_prevent < 0.2 and cond == 0):
        return RISCO_INTERMEDIARIO
    return RISCO_BAIXO


def recalculate_cronico(paciente):
    paciente.idade = calculate_age(paciente.data_nascimento)
    paciente.pas = extract_pas(paciente.ultima_pa)
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

    if (
        paciente.criterio_alto_risco
        or paciente.hac_descontrole
        or paciente.dm_descontrole
        or (paciente.imc is not None and paciente.imc > 39.9)
    ):
        paciente.classificacao_risco = "ALTO RISCO"
    elif (
        paciente.criterio_risco_intermediario
        or (paciente.idade is not None and paciente.idade >= 36)
        or (paciente.idade is not None and paciente.idade < 15)
    ):
        paciente.classificacao_risco = "RISCO INTERMEDIÁRIO"
    elif paciente.nome_paciente:
        paciente.classificacao_risco = "RISCO HABITUAL"
