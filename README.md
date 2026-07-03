# Estratificacao de Risco

Sistema web local em Flask para acompanhamento de pacientes cronicos, avaliacao Prevent, gestantes/puerperas, agentes ACS, relatorios, exportacao e backup.

O projeto foi preparado para rodar em um computador Windows e ser acessado por outros dispositivos da mesma rede Wi-Fi, sem expor o sistema para a internet.

## Funcionalidades

- Login obrigatorio com senha salva em hash.
- Criacao automatica do usuario administrador no primeiro uso.
- Banco SQLite local criado automaticamente.
- Cadastro e acompanhamento de pacientes cronicos.
- Calculo automatico de idade, PAS, risco cronico, exames cardiovasculares e Prevent.
- Cadastro e acompanhamento de gestantes, com IMC, IG, DPP e risco obstetrico automaticos.
- Cadastro de agentes ACS.
- Relatorios com filtros e exportacao para PDF e Excel.
- Backup do banco pela interface.
- Tema claro/escuro.
- Assets locais em `static/vendor`, sem depender de internet depois de instalado.

## Requisitos no Computador

```text
01. Python instalado
02. VS Code ou outro editor
03. Terminal do Windows, PowerShell ou CMD
04. Navegador instalado
05. Computador conectado no Wi-Fi
06. Celular/tablet conectado no mesmo Wi-Fi
07. Firewall liberando a porta 5000 apenas em rede privada
08. Nenhum redirecionamento de porta no roteador
09. Pasta do projeto criada
10. Ambiente virtual Python
11. Flask instalado
12. Waitress instalado
13. SQLite local
14. Arquivo .env configurado
15. Senha de administrador forte
```

## Instalar

No PowerShell, dentro da pasta do projeto:

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Se o PowerShell bloquear a ativacao do ambiente virtual, execute:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Depois tente ativar novamente.

## Configurar .env

Copie o arquivo de exemplo:

```powershell
Copy-Item .env.example .env
```

Edite o `.env` e troque principalmente:

```text
SECRET_KEY=troque_por_uma_chave_grande_e_aleatoria
ADMIN_USER=admin
ADMIN_PASSWORD=use_uma_senha_forte
DATABASE_URL=sqlite:///estratificacao_risco.db
```

No primeiro uso, o sistema cria o usuario administrador automaticamente com `ADMIN_USER` e `ADMIN_PASSWORD`. A senha e salva com hash no banco.

## Rodar Apenas Neste Computador

Para desenvolvimento local:

```powershell
venv\Scripts\Activate.ps1
python app.py
```

Acesse:

```text
http://127.0.0.1:5000
```

## Rodar na Rede Wi-Fi com Waitress

Uso recomendado para acesso por outros dispositivos na rede local:

```powershell
venv\Scripts\Activate.ps1
waitress-serve --host=0.0.0.0 --port=5000 app:app
```

Nao use `debug=True` na rede. O projeto ja deixa `python app.py` com `debug=False`, mas para rede local prefira Waitress.

## Descobrir o IP Local no Windows

No PowerShell ou CMD:

```powershell
ipconfig
```

Procure o IPv4 do adaptador Wi-Fi, por exemplo:

```text
Endereço IPv4 . . . . . . . . . . . . : 192.168.0.25
```

No celular conectado ao mesmo Wi-Fi, acesse:

```text
http://192.168.0.25:5000
```

Troque `192.168.0.25` pelo IP real do seu computador.

## Firewall do Windows

Permita a porta `5000` apenas em rede privada. Nao libere em rede publica.

Nunca faca:

```text
- port forwarding no roteador
- abertura da porta 5000 para internet
- compartilhamento do IP fora da rede local
```

## Backup

Dentro do sistema, use o item `Backup` no menu lateral. Ele cria uma copia do banco SQLite na pasta:

```text
backups/
```

O arquivo recebe data e hora no nome. A pasta `backups/` nao entra no GitHub por seguranca.

## Estrutura do Projeto

```text
Estratificacao_Risco/
├── app.py
├── models.py
├── requirements.txt
├── README.md
├── .env.example
├── .gitignore
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── dashboard.html
│   ├── lista_cronicos.html
│   ├── form_cronico.html
│   ├── lista_prevent.html
│   ├── form_prevent.html
│   ├── lista_gestantes.html
│   ├── form_gestante.html
│   ├── agentes.html
│   └── relatorios.html
├── static/
│   ├── app.js
│   ├── style.css
│   └── vendor/
│       ├── bootstrap.min.css
│       └── bootstrap.bundle.min.js
├── instance/
│   └── estratificacao_risco.db
└── backups/
```

`instance/`, `backups/`, `.env` e bancos `.db` ficam fora do GitHub.

## Comandos Principais

Instalar:

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Rodar local:

```powershell
python app.py
```

Rodar na rede Wi-Fi:

```powershell
waitress-serve --host=0.0.0.0 --port=5000 app:app
```

## Testes

O motor clinico (calculo PREVENT, CKD-EPI e estratificacao de risco) fica isolado em `domain.py` e tem cobertura de testes. Para rodar:

```powershell
venv\Scripts\Activate.ps1
python -m unittest discover -s tests
```

## Arquitetura

- `domain.py` — motor clinico puro (sem Flask/banco), testavel e auditavel.
- `security.py` — sessao, CSRF, chave secreta, throttle de login e criacao do admin.
- `models.py` — modelos SQLAlchemy, incluindo a tabela de auditoria.
- `app.py` — configuracao, rotas e geracao de relatorios.
- O schema do banco recebe colunas novas automaticamente na inicializacao (`ensure_schema`), sem quebrar bancos existentes.

## Usuarios

O sistema e multiusuario: cada profissional tem o proprio login, e a auditoria
registra quem fez cada acao (essencial para LGPD).

- **Papeis:** `Administrador` (gerencia usuarios) e `Usuario` (uso clinico).
- **Administrador** cria contas, redefine senhas, promove/rebaixa e ativa/desativa
  usuarios em `Usuarios` no menu lateral.
- **Cada usuario** troca a propria senha em `Trocar senha` (exige a senha atual).
- O sistema impede rebaixar, desativar ou excluir o **ultimo administrador ativo**,
  evitando ficar sem acesso.
- Ao desativar um usuario, a sessao dele e encerrada na proxima requisicao.
- O admin inicial e criado a partir do `.env` apenas na primeira execucao.

## Seguranca

- Todos os formularios sao protegidos contra CSRF (token por sessao).
- Cada acao e atribuida ao usuario autenticado que a executou.
- Login com hash de senha, limite de tentativas (5 por 5 min) e sessao obrigatoria em todas as rotas.
- Toda operacao de escrita (criar/editar/excluir/backup/login) e registrada na tabela de auditoria.
- A `SECRET_KEY` vem do `.env`; se ausente, e gerada e persistida em `instance/secret_key` (nunca um valor previsivel).
- Use senha forte no `.env`.
- Nunca use `debug=True` na rede.
- Nao abra porta no roteador nem faca port forwarding.
- Mantenha o sistema apenas em rede Wi-Fi confiavel.
- Faca backup periodico do banco; os arquivos em `backups/` contem dados sensiveis (PII) e nao devem sair da rede local.
- Nao envie `.env`, `instance/`, `backups/` ou arquivos `.db` para o GitHub.
- O admin e criado apenas na primeira execucao. Trocar `ADMIN_PASSWORD` no `.env` depois disso nao altera a senha existente; para redefinir, remova o usuario no banco e reinicie.
