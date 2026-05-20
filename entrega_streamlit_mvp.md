# Streamlit MVP — Mi7 Intelligence · Entrega Técnica

> **Status:** ✅ Pronto para apresentação  
> **Audiência:** Engenharia + Diretoria  
> **Repositório:** Público no GitHub — as diretrizes de segurança desta entrega são obrigatórias.

---

## Arquivos Criados / Modificados

| Arquivo | Ação | Propósito |
|---|---|---|
| `streamlit_app/app.py` | **NOVO** | Aplicação principal Streamlit |
| `streamlit_app/requirements.txt` | **NOVO** | Dependências do app Streamlit |
| `streamlit_app/.streamlit/secrets.toml` | **NOVO** | Template de credenciais (local apenas) |
| `.gitignore` | **MODIFICADO** | Proteção contra commit de secrets |

---

## Funcionalidade 1 — Upload de Múltiplos Arquivos

### Como funciona

O `st.file_uploader` é configurado com `accept_multiple_files=True`. O usuário pode selecionar vários `.json` de uma vez.

Para cada arquivo, o código navega **obrigatoriamente** pelo caminho:

```
raw JSON → ['response'] → ['pesquisas']  →  lista de registros
```

Cada arquivo gera um DataFrame parcial. Todos são concatenados com `pd.concat`:

```python
# Em carregar_multiplos_jsons()
frames = []
for conteudo in lista_de_bytes:
    raw = json.loads(conteudo.decode("utf-8"))
    registros = raw["response"]["pesquisas"]   # caminho fixo
    frames.append(pd.DataFrame(registros))

df_final = pd.concat(frames, ignore_index=True)
```

> [!NOTE]
> A função usa `@st.cache_data` — se o usuário reuplodar os mesmos arquivos, o Streamlit não reprocessa. Isso é crítico para arquivos grandes.

Se um arquivo vier com estrutura errada, um aviso é exibido na tela e o arquivo é **ignorado** (os outros continuam sendo processados).

---

## Funcionalidade 2 — Filtro de Data Interativo

### Como funciona

Na `st.sidebar`, há um checkbox `"Ativar filtro de data"` que habilita dois seletores (`st.date_input`). Enquanto desativado, os seletores ficam cinzas (`disabled=True`).

Quando ativado, o código:

1. Localiza a coluna de data no DataFrame (tolerante a variações de nome: `data`, `date`, `datacoleta`, `data_coleta`)
2. Converte para `datetime` com `pd.to_datetime(errors="coerce")` — datas inválidas viram `NaT` sem quebrar o app
3. Aplica o filtro por intervalo:

```python
mask = (
    (df["data"].dt.date >= data_inicio) &
    (df["data"].dt.date <= data_fim)
)
df_filtrado = df[mask]
```

O DataFrame filtrado é exibido com a contagem de registros restantes.

---

## Funcionalidade 3 — Consumo da API (PROCV/Merge)

### Como funciona

A função `_buscar_catalogo_api()` faz um GET na API do cliente e retorna um DataFrame `[codigoProduto, descricao]`.

O **merge** (Left Join) é feito com:

```python
df = df.merge(df_catalogo, on="codigoProduto", how="left")
df["descricao"] = df["descricao"].fillna("Não encontrado")
```

**Left Join** garante que:
- Todos os registros do JSON original são mantidos
- Produtos sem match na API recebem `"Não encontrado"`
- A análise **nunca falha** por causa de um produto sem cadastro

### Segurança da API

```
app.py  ──NÃO CONTÉM──►  URLs, Tokens, Credenciais
   ↓
st.secrets["irani"]["api_url"]    ← lido de secrets.toml (local) ou painel (cloud)
st.secrets["irani"]["api_token"]  ← lido de secrets.toml (local) ou painel (cloud)
```

Se os secrets não existirem, a análise continua normalmente **sem nomes de produto**, exibindo um aviso amigável.

---

## 🔐 Configuração de Segurança — Passo a Passo

### PASSO 1 — Verificar o `.gitignore`

O `.gitignore` já foi atualizado. Verifique se estas linhas estão presentes:

```gitignore
# STREAMLIT — SEGURANÇA (CRÍTICO)
**/.streamlit/secrets.toml
.streamlit/secrets.toml
```

> [!CAUTION]
> **NUNCA** remova essas linhas. Se o `secrets.toml` for commitado acidentalmente, o token do cliente Irani ficará público no GitHub. Revogue o token imediatamente se isso ocorrer.

### PASSO 2 — Criar o `secrets.toml` Local

1. Certifique-se que a pasta `streamlit_app/.streamlit/` existe
2. Abra o arquivo `streamlit_app/.streamlit/secrets.toml`
3. Preencha com as credenciais reais fornecidas pelo cliente:

```toml
[irani]
api_url   = "https://api.cliente-irani.com.br/v1.2/produto/listaprodutos/0/detalhado"
api_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."  # Token real aqui
```

4. **Confirme que o arquivo está no `.gitignore`** antes de qualquer `git add`:

```powershell
# Execute no terminal — se o arquivo aparecer, ESTÁ EM RISCO
git status --short | Select-String "secrets"

# Resultado esperado (arquivo ignorado pelo git): NENHUMA SAÍDA
```

### PASSO 3 — Testar Localmente

```powershell
# Na pasta streamlit_app/
cd c:\intelligence_mi7\streamlit_app
pip install -r requirements.txt
streamlit run app.py
```

O app abrirá em `http://localhost:8501`.

---

## ☁️ Configurando Secrets no Streamlit Cloud

> [!IMPORTANT]
> No Streamlit Cloud, **não existe** `secrets.toml`. As credenciais são cadastradas pelo painel web, com criptografia gerenciada pela plataforma.

**Passo a passo:**

1. Acesse [share.streamlit.io](https://share.streamlit.io) e faça login
2. Localize seu app → clique em **⋮ (três pontos)** → **Settings**
3. Vá na aba **Secrets**
4. Cole o conteúdo abaixo (substitua pelos valores reais):

```toml
[irani]
api_url   = "https://api.cliente-irani.com.br/v1.2/produto/listaprodutos/0/detalhado"
api_token = "SEU_TOKEN_REAL_AQUI"
```

5. Clique em **Save** → o app reiniciará automaticamente com as credenciais seguras

> [!NOTE]
> Os Secrets do Streamlit Cloud são armazenados criptografados e **nunca aparecem nos logs** nem no código-fonte. Apenas o app em execução consegue lê-los via `st.secrets`.

---

## Garantias de Segurança Implementadas

| Risco | Proteção Implementada |
|---|---|
| Token exposto no código | `st.secrets` — credencial nunca está no `app.py` |
| Commit acidental do `secrets.toml` | `.gitignore` com padrão `**/.streamlit/secrets.toml` |
| Log de dados sensíveis no terminal | Erros de API capturados com mensagens genéricas (`except` sem print da resposta bruta) |
| App quebrar sem API | `if df_catalogo is not None` — análise continua sem nomes de produto |
| Dados de rede expostos | Sem logging de `resp.text` ou `resp.json()` em caso de erro |
| Timeout de rede travando o app | `timeout=15` em todas as chamadas `requests.get()` |

---

## Estrutura Final de Arquivos

```
intelligence_mi7/
├── .gitignore                        ✅ Atualizado com proteção Streamlit
├── streamlit_app/
│   ├── app.py                        ✅ Aplicação principal
│   ├── requirements.txt              ✅ streamlit, pandas, requests
│   └── .streamlit/
│       └── secrets.toml              🔒 LOCAL APENAS — nunca no Git
└── app/                              (Flask original — intocado)
    └── services/
        └── analysis_service.py
```
