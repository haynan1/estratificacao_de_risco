"""Testes de integração da aplicação (auth, CSRF, RBAC, CRUD, ACS, paginação).

Roda contra um banco SQLite temporário e isolado — nunca toca o banco real.

Rodar:  python -m unittest discover -s tests
"""

import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Banco isolado ANTES de importar o app (o app cria a instância no import).
_TMP_DIR = tempfile.mkdtemp(prefix="estratificacao_test_")
_DB_PATH = os.path.join(_TMP_DIR, "test.db").replace("\\", "/")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "admin12345"
os.environ["SECRET_KEY"] = "chave-de-teste-fixa"

import app as appmod  # noqa: E402
from models import AvaliacaoPrevent, HistoricoRisco, PacienteCronico, PacienteIdoso, Usuario, db  # noqa: E402
from security import hash_senha  # noqa: E402
from utils import Page, paginate_list  # noqa: E402


def token(client, url):
    body = client.get(url).get_data(as_text=True)
    match = re.search(r'name="csrf_token" value="([a-f0-9]+)"', body)
    return match.group(1) if match else None


def login(client, username, password):
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token(client, "/login")},
        follow_redirects=False,
    )


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.app = appmod.app
        # Garante um usuário comum para os testes de RBAC.
        with self.app.app_context():
            if not Usuario.query.filter_by(username="ana").first():
                db.session.add(
                    Usuario(username="ana", nome="Ana", papel="padrao",
                            password_hash=hash_senha("ana12345"), ativo=True)
                )
                db.session.commit()

    def admin_client(self):
        c = self.app.test_client()
        self.assertEqual(login(c, "admin", "admin12345").status_code, 302)
        return c

    def user_client(self):
        c = self.app.test_client()
        self.assertEqual(login(c, "ana", "ana12345").status_code, 302)
        return c


class TestAuthCSRF(BaseCase):
    def test_auth_wall(self):
        self.assertEqual(self.app.test_client().get("/").status_code, 302)

    def test_csrf_blocks_post_without_token(self):
        c = self.app.test_client()
        r = c.post("/login", data={"username": "admin", "password": "admin12345"})
        self.assertEqual(r.status_code, 400)

    def test_login_ok_with_token(self):
        self.assertEqual(login(self.app.test_client(), "admin", "admin12345").status_code, 302)

    def test_wrong_password(self):
        self.assertEqual(login(self.app.test_client(), "admin", "errada").status_code, 200)

    def test_security_headers(self):
        r = self.app.test_client().get("/login")
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.headers.get("X-Frame-Options"), "SAMEORIGIN")


class TestRBAC(BaseCase):
    def test_padrao_bloqueado_em_admin(self):
        c = self.user_client()
        for url in ["/usuarios", "/auditoria"]:
            self.assertEqual(c.get(url).status_code, 403, url)

    def test_padrao_bloqueado_em_export_e_backup(self):
        c = self.user_client()
        self.assertEqual(c.get("/relatorios/exportar/excel").status_code, 403)
        self.assertEqual(c.get("/relatorios/exportar/pdf").status_code, 403)
        r = c.post("/backup", data={"csrf_token": token(c, "/")})
        self.assertEqual(r.status_code, 403)

    def test_admin_acessa_tudo(self):
        c = self.admin_client()
        for url in ["/usuarios", "/auditoria", "/idosos", "/relatorios/exportar/excel", "/relatorios/exportar/pdf"]:
            self.assertEqual(c.get(url).status_code, 200, url)

    def test_admin_ve_atalho_criar_usuario_no_menu(self):
        c = self.admin_client()
        body = c.get("/").get_data(as_text=True)
        self.assertIn("Criar usuário", body)
        self.assertIn('href="/usuarios"', body)

    def test_admin_master_nao_pode_ser_rebaixado(self):
        c = self.admin_client()
        with self.app.app_context():
            if not Usuario.query.filter_by(username="bruno_admin").first():
                db.session.add(
                    Usuario(username="bruno_admin", nome="Bruno", papel="admin",
                            password_hash=hash_senha("bruno12345"), ativo=True)
                )
                db.session.commit()
            master = Usuario.query.filter_by(papel="admin").order_by(Usuario.id.asc()).first()
            master_id = master.id

        r = c.post(
            f"/usuarios/{master_id}/papel",
            data={"csrf_token": token(c, "/usuarios")},
            follow_redirects=True,
        )

        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            master = db.session.get(Usuario, master_id)
            self.assertEqual(master.papel, "admin")
        self.assertIn("admin master", r.get_data(as_text=True).lower())


class TestCronicoCRUD(BaseCase):
    def test_cria_valida_e_bloqueia_cpf_duplicado(self):
        c = self.admin_client()
        tok = token(c, "/cronicos/novo")
        # CPF inválido
        r = c.post("/cronicos/novo", data={"nome_completo": "X", "cpf": "123", "csrf_token": tok})
        self.assertIn("CPF inválido", r.get_data(as_text=True))
        # CPF válido
        r = c.post("/cronicos/novo",
                   data={"nome_completo": "Paciente Teste", "cpf": "52998224725", "csrf_token": tok},
                   follow_redirects=True)
        self.assertIn("Paciente Teste", r.get_data(as_text=True))
        # duplicado → sem 500, com mensagem
        r = c.post("/cronicos/novo",
                   data={"nome_completo": "Outro", "cpf": "52998224725", "csrf_token": tok})
        self.assertEqual(r.status_code, 200)
        self.assertIn("já existe", r.get_data(as_text=True).lower())


    def test_prevent_incompleto_continua_pendente(self):
        c = self.admin_client()
        with self.app.app_context():
            paciente = PacienteCronico(
                nome_completo="Prevent Pendente",
                cpf="39053344705",
                risco_estratificado="Calcular Prevent",
                idade=55,
                pas=128,
            )
            db.session.add(paciente)
            db.session.flush()
            db.session.add(
                AvaliacaoPrevent(
                    paciente=paciente,
                    pas=128,
                    pas_calc_prevent=128,
                    risco_cardiovascular_10_anos="Inserir exames",
                )
            )
            paciente_risco_alto = PacienteCronico(
                nome_completo="Prevent Sem Exames",
                cpf="39053344888",
                risco_estratificado="Risco Alto",
                idade=54,
                pas=128,
            )
            db.session.add(paciente_risco_alto)
            db.session.flush()
            db.session.add(
                AvaliacaoPrevent(
                    paciente=paciente_risco_alto,
                    pas=128,
                    pas_calc_prevent=128,
                    risco_cardiovascular_10_anos="Inserir exames",
                )
            )
            db.session.commit()

        body = c.get("/prevent?status=pendente").get_data(as_text=True)
        self.assertIn("Prevent Pendente", body)
        self.assertIn("Prevent Sem Exames", body)
        self.assertIn("Pendente", body)

        body = c.get("/prevent?status=calculado").get_data(as_text=True)
        self.assertNotIn("Prevent Pendente", body)
        self.assertNotIn("Prevent Sem Exames", body)


    def test_cronico_novo_entra_na_fila_prevent_pendente(self):
        c = self.admin_client()
        with self.app.app_context():
            db.session.add(
                PacienteCronico(
                    nome_completo="Prevent Fila Padrao",
                    cpf="19120568191",
                    risco_estratificado="Risco Baixo",
                )
            )
            db.session.commit()

        prevent = c.get("/prevent?status=pendente").get_data(as_text=True)
        self.assertIn("Prevent Fila Padrao", prevent)
        self.assertIn("Pendente", prevent)

        cronicos = c.get("/cronicos").get_data(as_text=True)
        self.assertIn("Prevent Fila Padrao", cronicos)
        self.assertIn("PREVENT", cronicos)
        self.assertIn("Pendente", cronicos)


    def test_filtro_ercv_pendente_em_cronicos(self):
        c = self.admin_client()
        with self.app.app_context():
            db.session.add(
                PacienteCronico(
                    nome_completo="Paciente ERCV Pendente",
                    cpf="86729917025",
                    risco_estratificado="Calcular ERCV",
                )
            )
            db.session.add(
                PacienteCronico(
                    nome_completo="Paciente Risco Baixo",
                    cpf="51920849046",
                    risco_estratificado="Risco Baixo",
                )
            )
            db.session.commit()

        dashboard = c.get("/").get_data(as_text=True)
        self.assertIn("/cronicos?risco=Calcular+ERCV", dashboard)

        body = c.get("/cronicos?risco=Calcular%20ERCV").get_data(as_text=True)
        self.assertIn("Paciente ERCV Pendente", body)
        self.assertNotIn("Paciente Risco Baixo", body)


    def test_dashboard_card_ercv_sem_pendencia_nao_aplica_filtro_vazio(self):
        c = self.admin_client()
        with self.app.app_context():
            db.session.add(
                PacienteCronico(
                    nome_completo="Paciente Sem Pendencia",
                    cpf="19120568000",
                    risco_estratificado="Risco Baixo",
                )
            )
            db.session.commit()

        dashboard = c.get("/").get_data(as_text=True)
        self.assertIn('href="/cronicos"', dashboard)
        self.assertNotIn("/cronicos?risco=Calcular+ERCV", dashboard)

        body = c.get("/cronicos").get_data(as_text=True)
        self.assertIn("Paciente Sem Pendencia", body)


class TestACS(BaseCase):
    def test_agente_aparece_no_dropdown(self):
        c = self.admin_client()
        c.post("/agentes/novo",
               data={"nome": "Carla ACS", "micro_area": "12", "csrf_token": token(c, "/agentes")},
               follow_redirects=True)
        body = c.get("/cronicos/novo").get_data(as_text=True)
        self.assertIn("Carla ACS", body)


class TestIdosoCRUD(BaseCase):
    def test_cria_idoso_e_calcula_ivcf(self):
        c = self.admin_client()
        tok = token(c, "/idosos/novo")
        r = c.post(
            "/idosos/novo",
            data={
                "nome_completo": "Idoso Teste",
                "cpf": "52998224725",
                "data_nascimento": "1940-01-01",
                "sexo": "Feminino",
                "ivcf_banho": "on",
                "ivcf_comorbidades": "on",
                "ivcf_visao": "on",
                "csrf_token": tok,
            },
            follow_redirects=True,
        )
        body = r.get_data(as_text=True)
        self.assertIn("Idoso Teste", body)
        self.assertIn("Alto risco", body)

    def test_historico_idoso_registra_evolucao_do_risco(self):
        c = self.admin_client()
        tok = token(c, "/idosos/novo")
        r = c.post(
            "/idosos/novo",
            data={
                "nome_completo": "Idoso Historico",
                "cpf": "11144477735",
                "data_nascimento": "1940-01-01",
                "sexo": "Masculino",
                "ivcf_banho": "on",
                "ivcf_comorbidades": "on",
                "ivcf_visao": "on",
                "csrf_token": tok,
            },
            follow_redirects=True,
        )
        self.assertEqual(r.status_code, 200)

        with self.app.app_context():
            paciente = PacienteIdoso.query.filter_by(cpf="111.444.777-35").first()
            self.assertIsNotNone(paciente)
            paciente_id = paciente.id
            inicial = HistoricoRisco.query.filter_by(tipo="idoso", paciente_id=paciente_id).one()
            self.assertEqual(inicial.tendencia, "inicial")

        body = c.get(f"/pacientes/idoso/{paciente_id}/historico").get_data(as_text=True)
        self.assertIn("Idoso Historico", body)
        self.assertIn("Primeira avaliação", body)

        r = c.post(
            f"/idosos/editar/{paciente_id}",
            data={
                "nome_completo": "Idoso Historico",
                "cpf": "11144477735",
                "data_nascimento": "1940-01-01",
                "sexo": "Masculino",
                "csrf_token": token(c, f"/idosos/editar/{paciente_id}"),
            },
            follow_redirects=True,
        )
        self.assertEqual(r.status_code, 200)

        with self.app.app_context():
            tendencias = [
                h.tendencia
                for h in HistoricoRisco.query.filter_by(tipo="idoso", paciente_id=paciente_id)
                .order_by(HistoricoRisco.id.asc())
                .all()
            ]
        self.assertEqual(tendencias, ["inicial", "desceu"])


class TestPaginacao(unittest.TestCase):
    def test_paginate_list(self):
        itens = list(range(0, 125))
        p1 = paginate_list(itens, 1, 50)
        self.assertEqual(p1.pages, 3)
        self.assertEqual(len(p1.items), 50)
        self.assertFalse(p1.has_prev)
        self.assertTrue(p1.has_next)
        p3 = paginate_list(itens, 3, 50)
        self.assertEqual(len(p3.items), 25)
        self.assertTrue(p3.has_prev)
        self.assertFalse(p3.has_next)

    def test_page_vazia(self):
        p = paginate_list([], 1, 50)
        self.assertEqual(p.pages, 1)
        self.assertEqual(p.items, [])


if __name__ == "__main__":
    unittest.main()
