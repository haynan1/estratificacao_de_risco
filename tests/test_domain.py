"""Testes do motor clínico (domain.py).

Cobre parsing, cálculos obstétricos, CKD-EPI, escore PREVENT e a árvore de
estratificação de risco crônico. São a rede de segurança do que é clinicamente
consequente: um coeficiente trocado quebra um teste aqui, não um paciente.

Rodar:  python -m unittest discover -s tests
"""

import os
import sys
import unittest
from datetime import date
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import domain as d


def make_avaliacao(**kwargs):
    base = dict(
        idade_cal_prevent=None, ct_calc=None, hdl_calc_prevent=None,
        pas_calc_prevent=None, tfg_cal_prevent=None, tfg_ckd_epi=None,
        ct=None, hdl=None, pas=None, cr=None, ldl=None,
        dm2=False, fumante=False, anti_hipertensivo=False, uso_estatina=False,
        log_odds=None, data_exames=None, risco_cardiovascular_10_anos=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def make_paciente(**kwargs):
    base = dict(
        idade=50, sexo="M", data_nascimento=None,
        dm2=False, has=False, loa=False, dcv_at_sintomatica=False,
        ateroesclerose_subclinica=False, condicoes_alto_risco=0,
        num_eventos_previos=0, ultima_pa="120x80", pas=120, pad=80,
        ultima_hba1c=None,
        risco_estratificado=None, exames_cardiovasc=None, avaliacao_prevent=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestUtilidades(unittest.TestCase):
    def test_calculate_age(self):
        self.assertEqual(d.calculate_age(date(2000, 6, 1), date(2026, 7, 3)), 26)
        self.assertEqual(d.calculate_age(date(2000, 12, 1), date(2026, 7, 3)), 25)
        self.assertIsNone(d.calculate_age(None))

    def test_clamp(self):
        self.assertEqual(d.clamp(250, 90, 200), 200)
        self.assertEqual(d.clamp(50, 90, 200), 90)
        self.assertEqual(d.clamp(150, 90, 200), 150)
        self.assertIsNone(d.clamp(None, 90, 200))

    def test_subtract_months(self):
        self.assertEqual(d.subtract_months(date(2026, 3, 15), 6), date(2025, 9, 15))
        self.assertEqual(d.subtract_months(date(2026, 1, 31), 1), date(2025, 12, 31))

    def test_extract_pas(self):
        self.assertEqual(d.extract_pas("140x90"), 140)
        self.assertEqual(d.extract_pas("120/80"), 120)
        self.assertIsNone(d.extract_pas(""))

    def test_ig(self):
        self.assertEqual(d.parse_ig_days("12s + 3d"), 87)
        self.assertEqual(d.parse_ig_days("40s"), 280)
        self.assertEqual(d.format_ig(87), "12s + 3d")
        self.assertEqual(d.format_ig(None), "")

    def test_parse_percent_or_float(self):
        self.assertAlmostEqual(d.parse_percent_or_float("7.5%"), 0.075)
        self.assertAlmostEqual(d.parse_percent_or_float("0,2"), 0.2)
        self.assertAlmostEqual(d.parse_percent_or_float(0.1), 0.1)
        self.assertIsNone(d.parse_percent_or_float(""))
        self.assertIsNone(d.parse_percent_or_float("abc"))

    def test_display_risk(self):
        self.assertEqual(d.display_risk("Calcular Prevent"), "")
        self.assertEqual(d.display_risk("Risco Alto"), "Risco Alto")


class TestCKDEPI(unittest.TestCase):
    def test_valor_conhecido(self):
        # Homem, 50 anos, creatinina 0.9 (ratio = 1) → ~104 mL/min/1.73m².
        tfg = d.calculate_tfg_ckd_epi(0.9, 50, "M")
        self.assertAlmostEqual(tfg, 104.06, delta=1.0)

    def test_mulher_fator(self):
        tfg_m = d.calculate_tfg_ckd_epi(0.9, 50, "M")
        tfg_f = d.calculate_tfg_ckd_epi(0.9, 50, "F")
        # Mesma creatinina: mulher tem coeficientes distintos, resultado diferente.
        self.assertNotAlmostEqual(tfg_m, tfg_f, delta=0.5)

    def test_creatinina_invalida(self):
        self.assertIsNone(d.calculate_tfg_ckd_epi(0, 50, "M"))
        self.assertIsNone(d.calculate_tfg_ckd_epi(0.9, None, "M"))


class TestPrevent(unittest.TestCase):
    def _avaliacao_completa(self):
        return make_avaliacao(ct=200, hdl=45, pas=140, cr=0.9)

    def test_gera_percentual(self):
        pac = make_paciente(idade=60, sexo="M", risco_estratificado="Risco Baixo")
        av = self._avaliacao_completa()
        d.calculate_prevent(av, pac)
        self.assertTrue(av.risco_cardiovascular_10_anos.endswith("%"))

    def test_monotonico_por_idade(self):
        # Mesmo perfil, mais idade ⇒ maior risco cardiovascular.
        av1 = self._avaliacao_completa()
        av2 = self._avaliacao_completa()
        d.calculate_prevent(av1, make_paciente(idade=45, sexo="M", risco_estratificado="Risco Baixo"))
        d.calculate_prevent(av2, make_paciente(idade=70, sexo="M", risco_estratificado="Risco Baixo"))
        r1 = d.parse_percent_or_float(av1.risco_cardiovascular_10_anos)
        r2 = d.parse_percent_or_float(av2.risco_cardiovascular_10_anos)
        self.assertGreater(r2, r1)

    def test_monotonico_por_fumo(self):
        base = make_paciente(idade=60, sexo="M", risco_estratificado="Risco Baixo")
        nao_fuma = self._avaliacao_completa()
        fuma = make_avaliacao(ct=200, hdl=45, pas=140, cr=0.9, fumante=True)
        d.calculate_prevent(nao_fuma, base)
        d.calculate_prevent(fuma, base)
        self.assertGreater(
            d.parse_percent_or_float(fuma.risco_cardiovascular_10_anos),
            d.parse_percent_or_float(nao_fuma.risco_cardiovascular_10_anos),
        )

    def test_exames_incompletos(self):
        pac = make_paciente(risco_estratificado="Risco Baixo")
        av = make_avaliacao(ct=200, hdl=45)  # falta pas e cr
        d.calculate_prevent(av, pac)
        self.assertEqual(av.risco_cardiovascular_10_anos, "Inserir exames")

    def test_nao_aplicavel_para_muito_alto(self):
        pac = make_paciente(risco_estratificado=d.RISCO_MUITO_ALTO)
        av = self._avaliacao_completa()
        d.calculate_prevent(av, pac)
        self.assertEqual(av.risco_cardiovascular_10_anos, "Não aplicável")


class TestRiscoCronicoGoias(unittest.TestCase):
    """Estratificação conforme notas técnicas SES-GO (DM: Res. 1193/2025; HAS: NT 11/2021)."""

    def cls(self, **kw):
        return d.calculate_cronico_risk(make_paciente(**kw))

    # --- Cascata do DM (Quadro 9) ---
    def test_dm2_internacao_aguda_muito_alto(self):
        self.assertEqual(self.cls(dm2=True, internacao_aguda_12m=True), d.RISCO_MUITO_ALTO)

    def test_dm2_aterosclerose_muito_alto(self):
        self.assertEqual(self.cls(dm2=True, doenca_aterosclerotica=True), d.RISCO_MUITO_ALTO)

    def test_dm1_descontrolado_muito_alto(self):
        self.assertEqual(self.cls(dm1=True, ultima_hba1c=8.0), d.RISCO_MUITO_ALTO)

    def test_dm1_controlado_alto(self):
        self.assertEqual(self.cls(dm1=True, ultima_hba1c=7.0), d.RISCO_ALTO)

    def test_dm2_hba1c_alta_alto(self):
        self.assertEqual(self.cls(dm2=True, ultima_hba1c=8.0), d.RISCO_ALTO)

    def test_dm2_pressao_inadequada_alto(self):
        self.assertEqual(
            self.cls(dm2=True, ultima_hba1c=6.8, controle_pressorico_adequado=False), d.RISCO_ALTO
        )

    def test_dm2_ercv_alto(self):
        self.assertEqual(
            self.cls(dm2=True, ultima_hba1c=6.8, controle_pressorico_adequado=True, ercv_faixa="alto"),
            d.RISCO_ALTO,
        )

    def test_dm2_controlado_ercv_intermediario_medio(self):
        self.assertEqual(
            self.cls(dm2=True, ultima_hba1c=6.8, controle_pressorico_adequado=True,
                     ercv_faixa="intermediario"),
            d.RISCO_MEDIO,
        )

    def test_dm2_controlado_sem_ercv_pendente(self):
        self.assertEqual(
            self.cls(dm2=True, ultima_hba1c=6.8, controle_pressorico_adequado=True, ercv_faixa=""),
            d.ERCV_PENDENTE,
        )

    def test_pre_diabetes_autocuidado_ercv_baixo(self):
        self.assertEqual(
            self.cls(pre_diabetes=True, autocuidado_suficiente=True, ercv_faixa="baixo"), d.RISCO_BAIXO
        )

    def test_pre_diabetes_autocuidado_insuficiente_medio(self):
        self.assertEqual(
            self.cls(pre_diabetes=True, autocuidado_suficiente=False), d.RISCO_MEDIO
        )

    def test_pre_diabetes_sem_ercv_pendente(self):
        self.assertEqual(
            self.cls(pre_diabetes=True, autocuidado_suficiente=True, ercv_faixa=""), d.ERCV_PENDENTE
        )

    # --- Matriz da HAS (Quadro 3) ---
    def test_has_loa_alto(self):
        self.assertEqual(self.cls(has=True, sexo="F", loa=True), d.RISCO_ALTO)

    def test_has_pre_sem_fatores_sem_adicional(self):
        self.assertEqual(
            self.cls(has=True, sexo="F", idade=40, pas=132, pad=86), d.RISCO_SEM_ADICIONAL
        )

    def test_has_estagio1_sem_fatores_baixo(self):
        self.assertEqual(self.cls(has=True, sexo="F", idade=40, pas=145, pad=92), d.RISCO_BAIXO)

    def test_has_estagio3_sem_fatores_alto(self):
        self.assertEqual(self.cls(has=True, sexo="F", idade=40, pas=185, pad=115), d.RISCO_ALTO)

    def test_has_estagio1_tres_fatores_alto(self):
        self.assertEqual(
            self.cls(has=True, sexo="F", idade=40, pas=145, pad=92,
                     tabagismo=True, dislipidemia=True, obesidade=True),
            d.RISCO_ALTO,
        )

    def test_sem_dm_sem_has_baixo(self):
        self.assertEqual(self.cls(sexo="F"), d.RISCO_BAIXO)


class TestGestante(unittest.TestCase):
    def test_imc(self):
        self.assertAlmostEqual(d.calculate_imc(70, 170), 24.22, places=2)
        self.assertIsNone(d.calculate_imc(None, 170))

    def test_classificacao_alto_risco_por_criterio(self):
        g = SimpleNamespace(
            data_nascimento=date(1995, 1, 1), peso=65, estatura=165, grupo="Gestante",
            criterio_alto_risco=True, hac_descontrole=False, dm_descontrole=False,
            criterio_risco_intermediario=False, primeiro_usg=None, dum=None,
            ig_primeiro_usg=None, ig_atual_semanas=None, ig_semanas=None, dpp=None,
            idade=None, imc=None, classificacao_risco=None, nome_paciente="Teste",
        )
        d.recalculate_gestante(g)
        self.assertEqual(g.classificacao_risco, "ALTO RISCO")

    def test_classificacao_habitual(self):
        g = SimpleNamespace(
            data_nascimento=date(2000, 1, 1), peso=60, estatura=165, grupo="Gestante",
            criterio_alto_risco=False, hac_descontrole=False, dm_descontrole=False,
            criterio_risco_intermediario=False, primeiro_usg=None, dum=None,
            ig_primeiro_usg=None, ig_atual_semanas=None, ig_semanas=None, dpp=None,
            idade=None, imc=None, classificacao_risco=None, nome_paciente="Teste",
        )
        d.recalculate_gestante(g)
        self.assertEqual(g.classificacao_risco, "RISCO HABITUAL")

    # --- NT 6/2024 SES-GO: idade e faixas de IMC ---
    def _gestante(self, nascimento, peso, estatura):
        return SimpleNamespace(
            data_nascimento=nascimento, peso=peso, estatura=estatura, grupo="Gestante",
            criterio_alto_risco=False, hac_descontrole=False, dm_descontrole=False,
            criterio_risco_intermediario=False, primeiro_usg=None, dum=None,
            ig_primeiro_usg=None, ig_atual_semanas=None, ig_semanas=None, dpp=None,
            idade=None, imc=None, classificacao_risco=None, nome_paciente="Teste",
        )

    def _classificar(self, nascimento, peso=60, estatura=165):
        g = self._gestante(nascimento, peso, estatura)
        d.recalculate_gestante(g)
        return g.classificacao_risco

    def test_idade_38_e_habitual(self):
        # 15 a 40 anos é risco habitual (bug anterior classificava >=36 como intermediário).
        self.assertEqual(self._classificar(date(date.today().year - 38, 1, 1)), "RISCO HABITUAL")

    def test_idade_acima_de_40_e_intermediario(self):
        self.assertEqual(
            self._classificar(date(date.today().year - 41, 1, 1)), "RISCO INTERMEDIÁRIO"
        )

    def test_idade_abaixo_de_15_e_intermediario(self):
        self.assertEqual(
            self._classificar(date(date.today().year - 14, 1, 1)), "RISCO INTERMEDIÁRIO"
        )

    def test_imc_obesidade_grau1_e_intermediario(self):
        # IMC ~32 (30–39,9) sem comorbidade => intermediário.
        self.assertEqual(
            self._classificar(date(2000, 1, 1), peso=87, estatura=165), "RISCO INTERMEDIÁRIO"
        )

    def test_imc_baixo_peso_e_intermediario(self):
        # IMC ~17 (<18,5) => intermediário.
        self.assertEqual(
            self._classificar(date(2000, 1, 1), peso=49, estatura=170), "RISCO INTERMEDIÁRIO"
        )

    def test_imc_obesidade_grau3_e_alto(self):
        # IMC >=40 => alto risco.
        self.assertEqual(
            self._classificar(date(2000, 1, 1), peso=115, estatura=165), "ALTO RISCO"
        )


class TestERG(unittest.TestCase):
    """Escore de Risco Global (Framingham revisado, D'Agostino 2008)."""

    def test_valores_de_referencia(self):
        # ERG em pontos (Tabelas oficiais). Homem 55a, CT200, HDL45, PAS130 sem
        # fatores = 10+0+2+1 = 13 pontos -> 15,6%.
        self.assertAlmostEqual(
            d.calcular_erg("Masculino", 55, 200, 45, 130, False, False, False), 0.156, places=3
        )
        # Homem 40a, CT180, HDL50, PAS120 = 5-1+1+0 = 5 pontos -> 3,9%.
        self.assertAlmostEqual(
            d.calcular_erg("Masculino", 40, 180, 50, 120, False, False, False), 0.039, places=3
        )
        # Mulher 55a, CT200, HDL55, PAS125 = 8-1+3+0 = 10 pontos -> 6,3%.
        self.assertAlmostEqual(
            d.calcular_erg("Feminino", 55, 200, 55, 125, False, False, False), 0.063, places=3
        )

    def test_tratamento_e_fatores_aumentam_risco(self):
        base = d.calcular_erg("Masculino", 60, 220, 40, 140, False, False, False)
        tratado = d.calcular_erg("Masculino", 60, 220, 40, 140, True, False, False)
        fumante = d.calcular_erg("Masculino", 60, 220, 40, 140, False, True, False)
        self.assertGreater(tratado, base)
        self.assertGreater(fumante, base)

    def test_retorna_none_sem_dados(self):
        self.assertIsNone(d.calcular_erg("Masculino", 55, None, 45, 130, False, False, False))
        self.assertIsNone(d.calcular_erg("Masculino", None, 200, 45, 130, False, False, False))

    def test_faixas_homem(self):
        self.assertEqual(d.faixa_erg(0.049, "Masculino"), "baixo")
        self.assertEqual(d.faixa_erg(0.05, "Masculino"), "intermediario")
        self.assertEqual(d.faixa_erg(0.199, "Masculino"), "intermediario")
        self.assertEqual(d.faixa_erg(0.20, "Masculino"), "alto")

    def test_faixas_mulher(self):
        self.assertEqual(d.faixa_erg(0.049, "Feminino"), "baixo")
        self.assertEqual(d.faixa_erg(0.099, "Feminino"), "intermediario")
        self.assertEqual(d.faixa_erg(0.10, "Feminino"), "alto")


class TestIdosoIVCF(unittest.TestCase):
    def _idoso(self, **kw):
        base = dict(
            data_nascimento=date(1950, 1, 1),
            idade=None,
            ivcf_autopercepcao_ruim=False,
            ivcf_compras=False,
            ivcf_dinheiro=False,
            ivcf_domestico=False,
            ivcf_banho=False,
            ivcf_esquecimento=False,
            ivcf_esquecimento_piorando=False,
            ivcf_esquecimento_impede=False,
            ivcf_desanimo=False,
            ivcf_perda_interesse=False,
            ivcf_bracos=False,
            ivcf_objetos=False,
            ivcf_capacidade_aerobica=False,
            ivcf_marcha=False,
            ivcf_quedas=False,
            ivcf_incontinencia=False,
            ivcf_visao=False,
            ivcf_audicao=False,
            ivcf_comorbidades=False,
            ivcf_pontos=None,
            classificacao_ivcf=None,
            estrato_clinico_funcional=None,
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_ivcf_aivd_pontua_no_maximo_4(self):
        pac = self._idoso(idade=70, ivcf_compras=True, ivcf_dinheiro=True, ivcf_domestico=True)
        self.assertEqual(d.calcular_ivcf20(pac), 4)

    def test_ivcf_idade_e_dependencia_banho_alto(self):
        pac = self._idoso(idade=86, ivcf_banho=True, ivcf_comorbidades=True, ivcf_visao=True)
        self.assertEqual(d.calcular_ivcf20(pac), 15)
        self.assertEqual(d.classificar_ivcf20(15), d.IDOSO_ALTO)
        self.assertEqual(d.estrato_idoso_por_ivcf(15), d.IDOSO_FRAGIL)

    def test_faixas_ivcf(self):
        self.assertEqual(d.classificar_ivcf20(6), d.IDOSO_BAIXO)
        self.assertEqual(d.classificar_ivcf20(7), d.IDOSO_MODERADO)
        self.assertEqual(d.classificar_ivcf20(14), d.IDOSO_MODERADO)
        self.assertEqual(d.classificar_ivcf20(15), d.IDOSO_ALTO)

    def test_recalculate_idoso(self):
        pac = self._idoso(data_nascimento=date(date.today().year - 80, 1, 1), ivcf_quedas=True)
        d.recalculate_idoso(pac)
        self.assertGreaterEqual(pac.idade, 79)
        self.assertEqual(pac.ivcf_pontos, 3)
        self.assertEqual(pac.classificacao_ivcf, d.IDOSO_BAIXO)
        self.assertEqual(pac.estrato_clinico_funcional, d.IDOSO_ROBUSTO)


class TestFindrisc(unittest.TestCase):
    """Escore FINDRISC — risco de desenvolver DM2 em 10 anos (0–26 pontos)."""

    def fr(self, **kw):
        base = dict(
            idade=40, imc=22, cintura=90, sexo="Masculino", atividade_fisica=True,
            come_vegetais_diario=True, medicamento_pressao=False, glicemia_alta=False,
            familiar_diabetes="nao",
        )
        base.update(kw)
        return d.calcular_findrisc(**base)

    def test_perfil_saudavel_zero(self):
        self.assertEqual(self.fr(), 0)
        self.assertEqual(d.faixa_findrisc(0), "baixo")

    def test_perfil_maximo(self):
        pts = self.fr(idade=70, imc=35, cintura=110, atividade_fisica=False,
                      come_vegetais_diario=False, medicamento_pressao=True,
                      glicemia_alta=True, familiar_diabetes="primeiro_grau")
        self.assertEqual(pts, 26)  # 4+3+4+2+1+2+5+5
        self.assertEqual(d.faixa_findrisc(pts), "muito_alto")

    def test_caso_intermediario(self):
        # idade 50(2)+imc 27(1)+cintura 100 M(3)+vegetais não(1)+familiar 2º grau(3) = 10.
        pts = self.fr(idade=50, imc=27, cintura=100, come_vegetais_diario=False,
                      familiar_diabetes="segundo_grau")
        self.assertEqual(pts, 10)
        self.assertEqual(d.faixa_findrisc(pts), "levemente_elevado")

    def test_cintura_sexo_especifico(self):
        # cintura 85: mulher 80-88 → +3; homem <94 → 0.
        self.assertEqual(self.fr(sexo="Feminino", cintura=85), 3)
        self.assertEqual(self.fr(sexo="Masculino", cintura=85), 0)

    def test_none_sem_dados(self):
        self.assertIsNone(self.fr(imc=None))
        self.assertIsNone(self.fr(cintura=None))

    def test_faixas(self):
        self.assertEqual(d.faixa_findrisc(6), "baixo")
        self.assertEqual(d.faixa_findrisc(7), "levemente_elevado")
        self.assertEqual(d.faixa_findrisc(11), "levemente_elevado")
        self.assertEqual(d.faixa_findrisc(12), "moderado")
        self.assertEqual(d.faixa_findrisc(14), "moderado")
        self.assertEqual(d.faixa_findrisc(15), "alto")
        self.assertEqual(d.faixa_findrisc(20), "alto")
        self.assertEqual(d.faixa_findrisc(21), "muito_alto")


class TestComparaRisco(unittest.TestCase):
    """Direção da mudança de estratificação (base do histórico do paciente)."""

    def test_primeira_avaliacao(self):
        self.assertEqual(d.comparar_risco(None, d.RISCO_ALTO), "inicial")
        self.assertEqual(d.comparar_risco("", d.RISCO_BAIXO), "inicial")

    def test_reducao_de_risco(self):
        self.assertEqual(d.comparar_risco(d.RISCO_ALTO, d.RISCO_MEDIO), "desceu")
        self.assertEqual(d.comparar_risco(d.RISCO_MUITO_ALTO, d.RISCO_ALTO), "desceu")

    def test_aumento_de_risco(self):
        self.assertEqual(d.comparar_risco(d.RISCO_BAIXO, d.RISCO_ALTO), "subiu")
        self.assertEqual(d.comparar_risco(d.RISCO_SEM_ADICIONAL, d.RISCO_MODERADO), "subiu")

    def test_mesmo_nivel(self):
        # Rótulos diferentes, mesma gravidade clínica.
        self.assertEqual(d.comparar_risco(d.RISCO_MODERADO, d.RISCO_MEDIO), "manteve")

    def test_pendencia_nao_e_comparavel(self):
        self.assertEqual(d.comparar_risco(d.ERCV_PENDENTE, d.RISCO_MEDIO), "atualizado")
        self.assertEqual(d.comparar_risco(d.RISCO_MEDIO, d.ERCV_PENDENTE), "atualizado")

    def test_gestante_e_idoso(self):
        self.assertEqual(d.comparar_risco("ALTO RISCO", "RISCO HABITUAL"), "desceu")
        self.assertEqual(d.comparar_risco("RISCO HABITUAL", "ALTO RISCO"), "subiu")
        self.assertEqual(d.comparar_risco(d.IDOSO_FRAGIL, d.IDOSO_ROBUSTO), "desceu")
        self.assertEqual(
            d.comparar_risco(d.IDOSO_ROBUSTO, d.IDOSO_RISCO_FRAGILIZACAO), "subiu"
        )


if __name__ == "__main__":
    unittest.main()
