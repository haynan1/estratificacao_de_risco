# Estratificacao de Risco

Sistema web local em Flask para acompanhamento de pacientes cronicos, avaliacao
Prevent, gestantes/puerperas, agentes ACS, relatorios, exportacao e backup.

Feito para rodar em um computador Windows e ser acessado por outros dispositivos
(celular, tablet, outro PC) na mesma rede Wi-Fi, **sem** expor nada para a internet.

---

## Instalacao em uma maquina nova (passo a passo)

Siga na ordem. Cada passo tem o comando exato para copiar e colar.

### Passo 1 — Instalar o Python

1. Baixe o Python em https://www.python.org/downloads/ (versao 3.11 ou mais nova).
2. Ao instalar, **marque a caixa "Add Python to PATH"** na primeira tela. Isso e
   essencial; sem isso os comandos abaixo nao funcionam.
3. Conclua a instalacao e **reinicie** o computador (garante o PATH atualizado).
4. Confirme que funcionou abrindo o terminal e digitando:

   ```powershell
   python --version
   ```

   Deve aparecer algo como `Python 3.12.x`. Se der erro, reinstale marcando "Add to PATH".

### Passo 2 — Copiar o projeto para a maquina

Copie a pasta `Estratificacao_Risco` inteira para o novo computador (pen drive,
rede ou `git clone`). Guarde em um lugar fixo, por exemplo:

```text
C:\Sistemas\Estratificacao_Risco
```

Nao inclua as pastas `venv`, `instance`, `backups` nem o arquivo `.env` da maquina
antiga — eles sao recriados/reconfigurados aqui.

### Passo 3 — Abrir o terminal na pasta do projeto

No Windows Explorer, entre na pasta do projeto, clique na barra de endereco,
digite `powershell` e tecle Enter. O terminal ja abre no lugar certo.

Confirme que voce esta na pasta certa (deve listar `app.py`):

```powershell
dir
```

### Passo 4 — Criar e ativar o ambiente virtual

O ambiente virtual isola as bibliotecas do sistema.

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

Quando ativado, aparece `(venv)` no inicio da linha do terminal.

- **Se usar o Git Bash** (em vez do PowerShell), ative assim:

  ```bash
  source venv/Scripts/activate
  ```

- **Se o PowerShell bloquear** com erro de "execution policy", rode uma vez:

  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
  ```

  Responda `S` e ative novamente com `venv\Scripts\Activate.ps1`.

### Passo 5 — Instalar as dependencias

Com o `(venv)` ativo:

```powershell
pip install -r requirements.txt
```

Isso instala Flask, SQLAlchemy, openpyxl, reportlab, python-dotenv e waitress.
Nao e preciso instalar mais nada depois — o sistema nao depende da internet para funcionar.

### Passo 6 — Configurar o arquivo .env

O `.env` guarda apenas configuracoes tecnicas. **Voce nao precisa definir usuario
ou senha aqui** — isso e feito na tela de configuracao inicial (Passo 8).

```powershell
Copy-Item .env.example .env
```

Pode deixar o `.env` como veio. Se quiser, ajuste:

- `SECRET_KEY`: qualquer texto longo e aleatorio. Se deixar em branco, o sistema
  gera uma automaticamente e guarda em `instance/secret_key`.
- `DATABASE_URL`: pode deixar como esta.

### Passo 7 — Primeira execucao (cria o banco)

```powershell
python app.py
```

Na primeira vez o sistema cria sozinho o banco de dados SQLite e as tabelas
(pacientes, gestantes, agentes, usuarios, auditoria).

Como ainda nao existe administrador, o terminal exibe um **codigo de seguranca**,
assim:

```text
============================================================
  CONFIGURACAO INICIAL PENDENTE
  1. Abra no navegador:      http://<endereco-do-servidor>:5000/setup
  2. Codigo de seguranca:    AB7K-3XM2
     (informe este codigo na tela para criar o administrador)
============================================================
```

Anote esse codigo — ele so aparece no terminal do servidor e sera pedido na tela.
Deixe esse terminal aberto: enquanto estiver rodando, o sistema esta no ar.

### Passo 8 — Configuracao inicial (cria o administrador)

Abra o navegador e acesse:

```text
http://127.0.0.1:5000
```

O sistema abre sozinho o **assistente de configuracao inicial**. Ele explica o
sistema e pede o **codigo de seguranca** do terminal, seguido do nome, usuario e
senha do administrador. Ao concluir, o assistente se encerra (nao aparece de novo)
e voce entra com o usuario e a senha que acabou de criar.

> **Por que o codigo?** Enquanto nao ha administrador, a tela de configuracao fica
> acessivel na rede local. O codigo garante que so quem tem acesso ao servidor
> (onde o codigo e exibido) consegue criar a conta de administrador.

Pronto: o sistema esta instalado e funcionando neste computador.

Para conferir a versao que esta rodando a qualquer momento, acesse
`http://127.0.0.1:5000/versao`.

---

## Rodar no dia a dia

Sempre que for usar, abra o terminal na pasta do projeto, ative o ambiente e rode.

**Somente neste computador (teste rapido):**

```powershell
venv\Scripts\Activate.ps1
python app.py
```

**Para uso na clinica (recomendado — permite acesso pela rede):**

```powershell
venv\Scripts\Activate.ps1
waitress-serve --host=0.0.0.0 --port=5000 app:app
```

O Waitress e um servidor mais robusto que o modo de teste do Flask. Nunca use
`debug=True` em uso real na rede.

Para **parar** o sistema, volte ao terminal e tecle `Ctrl + C`.

---

## Acessar do celular / tablet na mesma rede

### 1. Descobrir o IP do computador

```powershell
ipconfig
```

Procure o `Endereco IPv4` do adaptador Wi-Fi, por exemplo `192.168.0.25`.

### 2. Liberar a porta no Firewall (uma vez so)

Abra o PowerShell **como administrador** e rode:

```powershell
New-NetFirewallRule -DisplayName "Estratificacao Risco 5000" -Direction Inbound -LocalPort 5000 -Protocol TCP -Action Allow -Profile Private
```

Isso libera a porta 5000 **apenas na rede privada** (Wi-Fi de casa/clinica).

### 3. Acessar pelo celular

Com o celular no **mesmo Wi-Fi**, abra no navegador (troque pelo seu IP real):

```text
http://192.168.0.25:5000
```

> O sistema precisa estar rodando com Waitress (`--host=0.0.0.0`) para aceitar
> conexoes de outros aparelhos.

---

## Primeiro login e criacao de usuarios

1. Entre como administrador (o usuario e a senha que voce criou na configuracao inicial).
2. No menu lateral, va em **Usuarios** (so aparece para administradores).
3. Em **Novo usuario**, cadastre cada profissional com login, senha inicial e papel:
   - **Administrador**: gerencia usuarios e ve a auditoria.
   - **Usuario**: uso clinico (pacientes, gestantes, relatorios).
4. Cada pessoa troca a propria senha em **Trocar senha** (rodape do menu).

Assim a auditoria registra corretamente quem fez cada acao.

---

## Backup dos dados

- No menu lateral, clique em **Backup**. O sistema gera uma copia do banco em:

  ```text
  backups/estratificacao_risco_ANO-MES-DIA_HORA.db
  ```

- Faca backup com frequencia e guarde uma copia em local seguro (pen drive, HD externo).
- Os arquivos de backup contem dados sensiveis (CPF, saude). **Nao** os envie para
  a internet nem para fora da rede da unidade.

---

## Atualizar para uma nova versao

Quando o codigo do sistema mudar:

1. Substitua os arquivos do projeto pelos novos (mantenha suas pastas `instance/`,
   `backups/` e o arquivo `.env`).
2. **Pare** o servidor antigo (`Ctrl + C` no terminal dele).
3. Reinstale dependencias, se o `requirements.txt` mudou:

   ```powershell
   venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

4. Suba de novo (`python app.py` ou o comando do Waitress).
5. No navegador, atualize com `Ctrl + F5` para limpar o cache.

O banco e atualizado automaticamente: colunas novas sao adicionadas sozinhas na
inicializacao, sem apagar os dados existentes.

---

## Resolucao de problemas

**"python nao e reconhecido como comando"**
O Python nao foi adicionado ao PATH. Reinstale marcando "Add Python to PATH" e reinicie.

**"Activate.ps1 nao pode ser carregado / execution policy"**
Rode `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` e ative novamente.

**A pagina nao abre ou mostra versao antiga**
O processo antigo pode ainda estar rodando. Verifique a versao em `/versao`.
Para encerrar o que estiver na porta 5000:

```powershell
Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

Depois suba de novo e atualize o navegador com `Ctrl + F5`.

**"That port is already in use" ao iniciar**
Ja existe um sistema rodando na porta 5000. Use o comando acima para encerra-lo,
ou rode em outra porta trocando `--port=5000` por outro numero.

**Celular nao acessa**
Confirme: (1) mesmo Wi-Fi; (2) rodando com Waitress `--host=0.0.0.0`; (3) regra de
firewall criada; (4) IP correto no `ipconfig`.

**Esqueci a senha do administrador**
Peca a outro administrador para resetar em **Usuarios**. Se nao houver outro admin,
apague o arquivo `instance/estratificacao_risco.db` (perde os dados) ou remova o
usuario admin direto no banco — ao reiniciar sem nenhum admin, o assistente de
configuracao inicial roda de novo e emite um novo codigo de seguranca no terminal.

---

## Estrutura do projeto

```text
Estratificacao_Risco/
├── app.py                 # configuracao e rotas
├── domain.py              # motor clinico (Prevent, CKD-EPI, risco) — testavel
├── security.py            # login, papeis, CSRF, chave secreta, throttle
├── reports.py             # relatorios e exportacao (Excel/PDF)
├── utils.py               # parsing, formatacao (CPF, datas) e paginacao
├── models.py              # modelos do banco (inclui usuarios e auditoria)
├── requirements.txt
├── README.md
├── .env.example
├── .gitignore
├── templates/
│   ├── base.html          # layout e menu lateral
│   ├── login.html
│   ├── dashboard.html
│   ├── lista_cronicos.html / form_cronico.html
│   ├── lista_prevent.html / form_prevent.html
│   ├── lista_gestantes.html / form_gestante.html
│   ├── agentes.html
│   ├── usuarios.html      # admin: gestao de usuarios
│   ├── conta.html         # trocar a propria senha
│   ├── auditoria.html     # admin: historico de acoes
│   ├── relatorios.html
│   └── _macros.html / _badge_risco.html
├── static/
│   ├── app.js
│   ├── style.css
│   └── vendor/            # Bootstrap local (funciona offline)
├── tests/
│   ├── test_domain.py     # testes do motor clinico
│   └── test_app.py        # testes de integracao (auth, CSRF, RBAC, CRUD)
├── instance/              # banco SQLite (criado automaticamente)
└── backups/               # copias geradas pela interface
```

`instance/`, `backups/`, `.env` e arquivos `.db` ficam fora do GitHub.

---

## Testes

O motor clinico (`domain.py`) e o comportamento da aplicacao (auth, CSRF, papeis,
CRUD) tem cobertura de testes. Rodam contra um banco temporario isolado, nunca no
banco real:

```powershell
venv\Scripts\Activate.ps1
python -m unittest discover -s tests
```

---

## Usuarios e papeis

O sistema e multiusuario: cada profissional tem o proprio login, e a auditoria
registra quem fez cada acao (essencial para LGPD).

- **Administrador** cria contas, redefine senhas, promove/rebaixa, ativa/desativa
  usuarios e ve a auditoria.
- **Usuario** faz o uso clinico do dia a dia (pacientes, gestantes, relatorios).
- Cada um troca a propria senha em **Trocar senha** (exige a senha atual).
- O sistema impede rebaixar, desativar ou excluir o **ultimo administrador ativo**.
- Ao desativar um usuario, a sessao dele e encerrada na proxima requisicao.
- **Backup e exportacao de relatorios (Excel/PDF) sao restritos ao administrador**,
  por lidarem com a base completa de dados sensiveis.

---

## Seguranca

- Login obrigatorio em todas as telas; senha salva com hash.
- Protecao CSRF em todos os formularios; headers de seguranca em toda resposta.
- Backup e exportacao de relatorios restritos ao administrador.
- Cada acao e registrada na auditoria com o usuario que a executou.
- Limite de tentativas de login (5 por 5 minutos).
- Listas paginadas (50 por pagina) para nao carregar toda a base de uma vez.
- `SECRET_KEY` vem do `.env`; se ausente, e gerada e persistida (nunca previsivel).
- Use senha forte no `.env` e nunca use `debug=True` na rede.
- Nao abra porta no roteador nem faca port forwarding.
- Mantenha o sistema apenas em rede Wi-Fi confiavel.
- Nao envie `.env`, `instance/`, `backups/` ou arquivos `.db` para a internet/GitHub.
