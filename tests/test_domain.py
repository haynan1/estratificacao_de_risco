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
        num_eventos_previos=0, ultima_pa="120x80", pas=120,
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


class TestRiscoCronico(unittest.TestCase):
    def test_extremo(self):
        self.assertEqual(d.calculate_cronico_risk(make_paciente(num_eventos_previos=2)), d.RISCO_EXTREMO)
        self.assertEqual(
            d.calculate_cronico_risk(make_paciente(num_eventos_previos=1, condicoes_alto_risco=2)),
            d.RISCO_EXTREMO,
        )

    def test_muito_alto(self):
        self.assertEqual(d.calculate_cronico_risk(make_paciente(num_eventos_previos=1)), d.RISCO_MUITO_ALTO)
        self.assertEqual(d.calculate_cronico_risk(make_paciente(condicoes_alto_risco=3)), d.RISCO_MUITO_ALTO)
        self.assertEqual(d.calculate_cronico_risk(make_paciente(dcv_at_sintomatica=True)), d.RISCO_MUITO_ALTO)
        self.assertEqual(d.calculate_cronico_risk(make_paciente(dm2=True, loa=True)), d.RISCO_MUITO_ALTO)

    def test_alto_por_ateroesclerose(self):
        self.assertEqual(
            d.calculate_cronico_risk(make_paciente(ateroesclerose_subclinica=True)), d.RISCO_ALTO
        )

    def test_pendente_prevent_sem_avaliacao(self):
        self.assertEqual(d.calculate_cronico_risk(make_paciente()), d.PREVENT_STATUS)

    def test_baixo_com_prevent_baixo(self):
        av = make_avaliacao(ct=200, hdl=45, pas=140, cr=0.9, risco_cardiovascular_10_anos="3%")
        pac = make_paciente(avaliacao_prevent=av)
        self.assertEqual(d.calculate_cronico_risk(pac), d.RISCO_BAIXO)

    def test_alto_com_prevent_elevado(self):
        av = make_avaliacao(ct=250, hdl=35, pas=180, cr=1.2, risco_cardiovascular_10_anos="25%")
        pac = make_paciente(avaliacao_prevent=av)
        self.assertEqual(d.calculate_cronico_risk(pac), d.RISCO_ALTO)


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


if __name__ == "__main__":
    unittest.main()
