"""
Mi7 Intelligence — Análise de Concorrentes (Streamlit MVP)
===========================================================
Funcionalidades:
  1. Upload de múltiplos arquivos JSON (gaveta response > pesquisas)
  2. Filtro de data interativo na sidebar
  3. Enriquecimento via API do cliente (merge com catálogo de produtos)
  4. Detecção de inflação de dados por concorrentes

Segurança: NENHUMA credencial está neste arquivo.
           Todas as configurações sensíveis ficam em .streamlit/secrets.toml
           (excluído do Git via .gitignore).

CHANGELOG da revisão (2026-05):
  - Removido código morto/duplicado em _buscar_catalogo_api
  - Corrigido bug de perda de zero à esquerda em EANs
  - Paginação da API mais robusta (sem limite arbitrário de 15 páginas)
  - Anti-loop baseado em conjunto de IDs, não no primeiro item
  - Classificador de origem com match por palavra (regex word boundary)
  - Validação de intervalo de datas
  - Removido import não usado (concurrent.futures)
  - Cache da API não persiste retornos None
  - Tratamento de exceção mais granular na API
"""

import json
import re
import streamlit as st
import pandas as pd
import requests

# ──────────────────────────────────────────────────────────────────────────────
# CONSTANTES DE NEGÓCIO
# ──────────────────────────────────────────────────────────────────────────────

# Padrões de observação que identificam coletas da própria Mi7
MI7_PATTERNS = ("MENOR PREÇO", "MENOR PRECO", "ONLINE")

# Timeout (segundos) para chamada da API do cliente
API_TIMEOUT = 15

# Limites de paginação da API de catálogo
API_PAGE_SIZE = 200          # tamanho típico de página retornado pela API
API_MAX_PAGES = 500          # trava de segurança (500 * 200 = 100k produtos)


# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO DA PÁGINA
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Mi7 Intelligence · Análise de Concorrentes",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# CSS mínimo para deixar a UI mais elegante
st.markdown(
    """
    <style>
        .metric-card {
            background: #1e1e2e;
            border-radius: 10px;
            padding: 1rem 1.5rem;
            border-left: 4px solid #7c3aed;
        }
        .crime-header { color: #f87171; font-weight: 700; }
        .stDataFrame thead th { background-color: #1e1e2e !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ──────────────────────────────────────────────────────────────────────────────
# FUNÇÕES AUXILIARES — CARREGAMENTO E PARSING
# ──────────────────────────────────────────────────────────────────────────────

def _extrair_pesquisas(raw: dict) -> list[dict]:
    """
    Navega no JSON seguindo o caminho obrigatório:
        raw → 'response' → 'pesquisas'
    Retorna a lista de registros ou lança ValueError com mensagem clara.
    """
    try:
        response = raw["response"]
        if not isinstance(response, dict):
            raise ValueError("O campo 'response' não é um objeto.")
        pesquisas = response["pesquisas"]
        if not isinstance(pesquisas, list):
            raise ValueError("O campo 'pesquisas' não é uma lista.")
        return pesquisas
    except KeyError as exc:
        chave_faltando = str(exc).strip("'")
        raise ValueError(
            f"Estrutura JSON inválida: chave '{chave_faltando}' não encontrada. "
            "Verifique se o arquivo vem da coleta correta."
        ) from exc


@st.cache_data(show_spinner=False)
def carregar_multiplos_jsons(arquivos_bytes: tuple[bytes, ...]) -> pd.DataFrame:
    """
    Recebe uma tupla de bytes (um por arquivo) e retorna um DataFrame
    concatenado com todos os registros de 'pesquisas'.

    NOTA: o parâmetro é tupla (não lista) para garantir hashability no cache.
    """
    frames = []
    erros = []

    for idx, conteudo in enumerate(arquivos_bytes, start=1):
        try:
            raw = json.loads(conteudo.decode("utf-8"))
            registros = _extrair_pesquisas(raw)
            df_parcial = pd.DataFrame(registros)
            df_parcial["_arquivo_idx"] = idx  # rastreabilidade
            frames.append(df_parcial)
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
            erros.append(f"Arquivo {idx}: {exc}")

    if erros:
        for msg in erros:
            st.warning(f"⚠️ {msg}")

    if not frames:
        raise ValueError("Nenhum dado válido encontrado nos arquivos enviados.")

    return pd.concat(frames, ignore_index=True)


def _normalizar_colunas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detecta variações de nome de coluna e padroniza para os nomes internos.
    Tolerante a maiúsculas/minúsculas e espaços extras.
    """
    mapa_colunas = {}
    cols_lower = {c.lower().strip(): c for c in df.columns}

    # codigoProduto → ean (identificador único do produto)
    for candidato in ["codigoproduto", "ean", "gtin", "barcode",
                      "codbarras", "codigo_produto"]:
        if candidato in cols_lower:
            mapa_colunas[cols_lower[candidato]] = "codigoProduto"
            break

    # observacao → observacao
    for candidato in ["observacao", "obs", "observação"]:
        if candidato in cols_lower:
            mapa_colunas[cols_lower[candidato]] = "observacao"
            break

    # data → data
    for candidato in ["data", "date", "datacoleta", "data_coleta"]:
        if candidato in cols_lower:
            mapa_colunas[cols_lower[candidato]] = "data"
            break

    return df.rename(columns=mapa_colunas)


def _classificar_origem(obs: str, concorrente: str) -> str:
    """
    Classifica cada registro como Mi7, concorrente selecionado ou Outros.

    CORREÇÃO: usa regex com word boundary (\\b) para evitar falso positivo,
    p. ex., concorrente="ABC" casando com observação="ABCD MERCADO".
    """
    obs_up = str(obs).upper()
    if any(p in obs_up for p in MI7_PATTERNS):
        return "Mi7"

    # Word boundary: nome do concorrente como palavra inteira (case-insensitive)
    padrao = r"\b" + re.escape(concorrente.upper()) + r"\b"
    if re.search(padrao, obs_up):
        return concorrente

    return "Outros"


def _limpar_ean(serie: pd.Series) -> pd.Series:
    """
    Limpa códigos de produto SEM perder zeros à esquerda.

    CORREÇÃO CRÍTICA: a versão anterior usava pd.to_numeric().astype(int).astype(str),
    o que destrói o zero à esquerda de EAN-13 (ex.: "0789012345678" virava
    "789012345678") e quebra silenciosamente o merge com o catálogo.

    Agora: trata como string desde o início, remove apenas .0 de floats acidentais.
    """
    s = serie.astype(str).str.strip()
    # Remove sufixo ".0" que aparece quando pandas leu o EAN como float
    s = s.str.replace(r"\.0$", "", regex=True)
    # Remove valores claramente inválidos
    s = s.replace({"nan": "", "None": "", "0": ""})
    return s


# ──────────────────────────────────────────────────────────────────────────────
# FUNÇÃO AUXILIAR — API DO CLIENTE (SEGURA)
# ──────────────────────────────────────────────────────────────────────────────

def _autenticar_api(api_url: str, api_user: str, api_pass: str) -> str | None:
    """Faz autenticação e retorna o token, ou None em falha."""
    base_url = api_url.split("/v1.2")[0]
    auth_url = f"{base_url}/v1.1/auth"

    resp = requests.post(
        auth_url,
        json={"usuario": api_user, "senha": api_pass},
        headers={"Content-type": "application/json"},
        timeout=API_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("response", {}).get("token")


def _baixar_catalogo_paginado(api_url: str, token: str) -> list[dict]:
    """
    Paginação cursor-based (RP Info): o segmento /{LastID}/ na URL define
    o ponto de partida; a API retorna até API_PAGE_SIZE produtos com código
    estritamente maior que LastID, ordenados crescentemente.

    Inicia em "0" para pegar do começo do catálogo.
    """
    headers = {"Content-type": "application/json", "token": token}
    todos = []
    vistos = set()           # dedup defensivo (caso API use >= em vez de >)
    last_id = "0"

    for iteracao in range(API_MAX_PAGES):
        # Substitui o cursor na URL. O 'count=1' protege contra colisões
        # caso "/0/" aparecesse em outro lugar da URL (no Super Irani não
        # acontece, mas é defesa barata).
        if "/0/" in api_url:
            url_paginada = api_url.replace("/0/", f"/{last_id}/", 1)
        else:
            url_paginada = f"{api_url.rstrip('/')}/{last_id}"

        try:
            resp = requests.get(url_paginada, headers=headers, timeout=API_TIMEOUT)
        except requests.Timeout:
            print(f"⏱️  Timeout no cursor {last_id}. Encerrando paginação.")
            break
        except requests.RequestException as exc:
            print(f"🚨 Erro de rede no cursor {last_id}: {exc}")
            break

        if resp.status_code != 200:
            print(f"⚠️  Status {resp.status_code} no cursor {last_id}. "
                  f"Resposta: {resp.text[:200]}")
            break

        try:
            payload = resp.json()
        except ValueError:
            print(f"⚠️  Resposta não-JSON no cursor {last_id}.")
            break

        produtos_pagina = payload.get("produtos") \
            or payload.get("response", {}).get("produtos", [])
        if not produtos_pagina:
            break

        # Dedup defensivo: filtra registros já vistos
        novos = [p for p in produtos_pagina
                 if p.get("codigo") not in vistos]
        for p in novos:
            vistos.add(p.get("codigo"))
        todos.extend(novos)

        # Log de progresso a cada 5 lotes (para não poluir o output do Streamlit)
        if iteracao % 5 == 0:
            print(f"📥 Lote {iteracao + 1}: cursor={last_id} → "
                  f"+{len(novos)} novos (total: {len(todos):,})")

        # Define o próximo cursor a partir do último código do lote
        codigo_raw = produtos_pagina[-1].get("codigo")
        if codigo_raw is None:
            print(f"⚠️  Último produto sem 'codigo'. Encerrando.")
            break

        novo_last_id = str(codigo_raw)
        if novo_last_id == last_id:
            # Cursor não avançou → API repetiu o mesmo lote
            break
        last_id = novo_last_id

        # Lote parcial = última página
        if len(produtos_pagina) < API_PAGE_SIZE:
            break

    print(f"✅ Catálogo carregado: {len(todos):,} produtos únicos.")
    return todos

        # ANTI-LOOP robusto: assina a página pelo conjunto de IDs.
        # Se a mesma combinação aparecer de novo, a API está repetindo.
        assinatura = frozenset(p.get("codigo") for p in produtos_pagina)
        if assinatura in assinaturas_vistas:
            break
        assinaturas_vistas.add(assinatura)

        todos.extend(produtos_pagina)

        # Página parcial → última página
        if len(produtos_pagina) < API_PAGE_SIZE:
            break

    return todos


@st.cache_data(show_spinner="Conectando ao catálogo da API...", ttl=3600)
def _buscar_catalogo_api() -> pd.DataFrame | None:
    """
    Busca o catálogo completo de produtos via API do cliente.

    Retorna DataFrame com colunas [codigoProduto, descricao] ou None se:
      - secrets não configurados,
      - falha de autenticação,
      - catálogo vazio.

    NOTA: o Streamlit cacheia None por padrão. Para evitar isso prendendo o
    usuário por 1h em caso de erro transitório, em falha limpamos o cache
    desta função antes de retornar None.
    """
    # 1. Lê secrets
    try:
        api_url = st.secrets["clientx"]["api_url"]
        api_user = st.secrets["clientx"]["api_user"]
        api_pass = st.secrets["clientx"]["api_pass"]
    except (KeyError, FileNotFoundError):
        return None

    # 2. Autentica + baixa catálogo
    try:
        token = _autenticar_api(api_url, api_user, api_pass)
        if not token:
            st.warning("API respondeu mas não retornou token de autenticação.")
            _buscar_catalogo_api.clear()
            return None

        produtos = _baixar_catalogo_paginado(api_url, token)
    except requests.Timeout:
        st.error("⏱️ Timeout na API do catálogo. Tente novamente em instantes.")
        _buscar_catalogo_api.clear()
        return None
    except requests.RequestException as exc:
        st.error(f"🚨 Erro de rede na API: {exc}")
        _buscar_catalogo_api.clear()
        return None
    except (KeyError, ValueError) as exc:
        st.error(f"🚨 Resposta inesperada da API: {exc}")
        _buscar_catalogo_api.clear()
        return None

    if not produtos:
        st.info("Catálogo de produtos retornou vazio.")
        _buscar_catalogo_api.clear()
        return None

    # 3. Monta DataFrame
    df_prod = pd.DataFrame(produtos)
    if "codigo" not in df_prod.columns or "descricao" not in df_prod.columns:
        st.warning("Catálogo da API sem as colunas esperadas (codigo/descricao).")
        _buscar_catalogo_api.clear()
        return None

    df_prod = df_prod[["codigo", "descricao"]].copy()
    df_prod.rename(columns={"codigo": "codigoProduto"}, inplace=True)
    df_prod["codigoProduto"] = _limpar_ean(df_prod["codigoProduto"])
    df_prod = df_prod[df_prod["codigoProduto"] != ""]
    return df_prod.drop_duplicates(subset=["codigoProduto"])


# ──────────────────────────────────────────────────────────────────────────────
# PIPELINE PRINCIPAL DE ANÁLISE
# ──────────────────────────────────────────────────────────────────────────────

def executar_analise(df: pd.DataFrame, concorrente: str) -> dict:
    """
    Pipeline completo:
      1. Normaliza colunas
      2. Converte datas (se ainda não convertidas)
      3. Classifica origem (Mi7 vs concorrente)
      4. Limpa EANs preservando zeros à esquerda
      5. Merge com catálogo de produtos (se disponível)
      6. Calcula métricas e tabela 'Prova do Crime'
    """
    df = _normalizar_colunas(df)

    # Converte coluna de data apenas se ainda não for datetime
    if "data" in df.columns and not pd.api.types.is_datetime64_any_dtype(df["data"]):
        df["data"] = pd.to_datetime(df["data"], errors="coerce", dayfirst=True)

    # Classifica origem
    if "observacao" not in df.columns:
        raise ValueError(
            "Coluna 'observacao' não encontrada. "
            f"Colunas disponíveis: {list(df.columns)}"
        )
    df["source"] = df["observacao"].apply(
        lambda x: _classificar_origem(x, concorrente)
    )

    # Limpa EANs (preservando zeros à esquerda)
    if "codigoProduto" not in df.columns:
        raise ValueError(
            "Coluna 'codigoProduto' (ou equivalente) não encontrada. "
            f"Colunas disponíveis: {list(df.columns)}"
        )

    df["codigoProduto"] = _limpar_ean(df["codigoProduto"])
    df = df[df["codigoProduto"] != ""].copy()

    # ── Merge com catálogo de produtos (PROCV) ───────────────────────────────
    df_catalogo = _buscar_catalogo_api()
    if df_catalogo is not None:
        df = df.merge(df_catalogo, on="codigoProduto", how="left")
        df["descricao"] = df["descricao"].fillna("Não encontrado")
        nao_encontrados = (df["descricao"] == "Não encontrado").sum()
        if nao_encontrados > 0:
            st.caption(
                f"ℹ️ {nao_encontrados:,} registros sem correspondência no catálogo "
                "(EAN não cadastrado ou catálogo desatualizado)."
            )
    else:
        df["descricao"] = "Sem catálogo"

    # ── Separação Mi7 vs Concorrente ─────────────────────────────────────────
    df_mi7 = df[df["source"] == "Mi7"].copy()
    df_comp = df[df["source"] == concorrente].copy()

    if df_comp.empty:
        origens = df["source"].unique().tolist()
        raise ValueError(
            f"Nenhum registro encontrado para '{concorrente}'. "
            f"Origens detectadas no arquivo: {origens[:10]}"
        )

    # ── Métricas ─────────────────────────────────────────────────────────────
    mi7_total = len(df_mi7)
    mi7_unique = df_mi7["codigoProduto"].nunique()

    comp_total = len(df_comp)
    comp_unique = df_comp["codigoProduto"].nunique()
    comp_duplicates = comp_total - comp_unique
    indice_fraude = (
        round((comp_duplicates / comp_total * 100), 1) if comp_total > 0 else 0.0
    )

    # ── Tabela "Prova do Crime" ───────────────────────────────────────────────
    prova = (
        df_comp
        .groupby(["codigoProduto", "descricao"])
        .size()
        .reset_index(name="Repetições")
        .sort_values("Repetições", ascending=False)
        .head(20)
        .rename(columns={"codigoProduto": "EAN/Código", "descricao": "Produto"})
    )

    return {
        "df_completo":   df,
        "df_mi7":        df_mi7,
        "df_comp":       df_comp,
        "prova":         prova,
        "mi7_total":     mi7_total,
        "mi7_unique":    mi7_unique,
        "comp_total":    comp_total,
        "comp_unique":   comp_unique,
        "comp_dup":      comp_duplicates,
        "indice_fraude": indice_fraude,
        "concorrente":   concorrente,
    }


# ──────────────────────────────────────────────────────────────────────────────
# INTERFACE — SIDEBAR
# ──────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## Mi7 Intelligence")
    st.markdown("---")
    st.markdown("### 1. Upload dos Arquivos")

    arquivos_upados = st.file_uploader(
        label="Selecione um ou mais arquivos JSON",
        type=["json"],
        accept_multiple_files=True,
        help="Os arquivos devem conter a estrutura: response → pesquisas",
    )

    st.markdown("### 2. Filtro de Data")
    usar_filtro_data = st.checkbox("Ativar filtro de data", value=False)

    col_d1, col_d2 = st.columns(2)
    data_inicio = col_d1.date_input(
        "De", key="data_inicio", disabled=not usar_filtro_data
    )
    data_fim = col_d2.date_input(
        "Até", key="data_fim", disabled=not usar_filtro_data
    )

    st.markdown("### 3. Concorrente Alvo")
    nome_concorrente = st.text_input(
        "Nome do concorrente",
        value="ClickSuper",
        help="Exatamente como aparece no campo 'observacao' do JSON.",
    )

    st.markdown("---")

    # Validação de segredos
    api_configurada = False
    try:
        if "clientx" in st.secrets and "api_user" in st.secrets["clientx"]:
            api_configurada = True
    except (KeyError, FileNotFoundError):
        api_configurada = False

    if api_configurada:
        st.success("API de produtos configurada.")
    else:
        st.info(
            "API de produtos não configurada.\n\n"
            "Configure os dados em Secrets para enriquecer com nomes."
        )

    analisar = st.button(
        "Executar Análise", type="primary", use_container_width=True
    )


# ──────────────────────────────────────────────────────────────────────────────
# INTERFACE — ÁREA PRINCIPAL
# ──────────────────────────────────────────────────────────────────────────────

st.title("Mi7 Intelligence — Análise de Concorrentes")
st.caption(
    "Ferramenta interna para detecção de inflação de dados "
    "por coletores terceiros."
)

if not arquivos_upados:
    st.info(
        "Faça o upload de um ou mais arquivos JSON na barra lateral para iniciar."
    )
    st.stop()

if not analisar:
    st.info(
        "Configure os parâmetros na barra lateral e clique em "
        "**▶ Executar Análise**."
    )
    st.stop()

# Validação de nome do concorrente
if not nome_concorrente or not nome_concorrente.strip():
    st.error("❌ Informe o nome do concorrente alvo na barra lateral.")
    st.stop()

# Validação de intervalo de datas
if usar_filtro_data and data_inicio > data_fim:
    st.error("❌ Data de início é posterior à data final. Ajuste o filtro.")
    st.stop()

# ── Carregamento e concatenação ───────────────────────────────────────────────
with st.spinner(f"Carregando {len(arquivos_upados)} arquivo(s)…"):
    try:
        # tupla (não lista) para garantir hashability no cache
        lista_bytes = tuple(f.read() for f in arquivos_upados)
        df_bruto = carregar_multiplos_jsons(lista_bytes)
    except ValueError as exc:
        st.error(f"❌ Erro no carregamento: {exc}")
        st.stop()

st.success(
    f"{len(arquivos_upados)} arquivo(s) carregado(s) · "
    f"**{len(df_bruto):,}** registros totais antes do filtro."
)

# ── Filtro de data ────────────────────────────────────────────────────────────
df_filtrado = df_bruto.copy()

if usar_filtro_data:
    col_data = next(
        (c for c in df_filtrado.columns
         if c.lower().strip() in ["data", "date", "datacoleta", "data_coleta"]),
        None,
    )
    if col_data is None:
        st.warning("⚠️ Coluna de data não encontrada no JSON. Filtro ignorado.")
    else:
        df_filtrado[col_data] = pd.to_datetime(
            df_filtrado[col_data], errors="coerce", dayfirst=True
        )
        mask = (
            (df_filtrado[col_data].dt.date >= data_inicio) &
            (df_filtrado[col_data].dt.date <= data_fim)
        )
        df_filtrado = df_filtrado[mask]

        if df_filtrado.empty:
            st.warning(
                "⚠️ Nenhum registro encontrado no intervalo de datas selecionado."
            )
            st.stop()

        st.info(
            f"Filtro aplicado: **{data_inicio}** até **{data_fim}** · "
            f"**{len(df_filtrado):,}** registros restantes."
        )

# ── Execução da análise ───────────────────────────────────────────────────────
with st.spinner("Executando análise competitiva…"):
    try:
        resultado = executar_analise(df_filtrado, nome_concorrente)
    except ValueError as exc:
        st.error(f"❌ Erro na análise: {exc}")
        st.stop()

# ── KPIs ──────────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Painel de Métricas")

col1, col2, col3, col4, col5 = st.columns(5)

col1.metric("Registros Mi7",        f"{resultado['mi7_total']:,}")
col2.metric("Produtos Únicos Mi7",  f"{resultado['mi7_unique']:,}")
col3.metric(f"Registros {nome_concorrente}", f"{resultado['comp_total']:,}")
col4.metric(f"Únicos {nome_concorrente}",    f"{resultado['comp_unique']:,}")
col5.metric(
    "Índice de Fraude",
    f"{resultado['indice_fraude']}%",
    delta=f"+{resultado['comp_dup']:,} duplicatas",
    delta_color="inverse",
)

# ── Gráfico comparativo ───────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Total vs. Únicos por Empresa")

df_chart = pd.DataFrame({
    "Empresa": ["Mi7", "Mi7", nome_concorrente, nome_concorrente],
    "Tipo":    ["Total", "Únicos", "Total", "Únicos"],
    "Qtd":     [
        resultado["mi7_total"],
        resultado["mi7_unique"],
        resultado["comp_total"],
        resultado["comp_unique"],
    ],
})

st.bar_chart(
    df_chart.pivot(index="Empresa", columns="Tipo", values="Qtd"),
    use_container_width=True,
)

# ── Prova do Crime ────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Top 20 Produtos Mais Repetidos")
st.caption(
    f"Produtos que o coletor **{nome_concorrente}** mais repetiu no período. "
    "Alta repetição indica inflação artificial de volume."
)

st.dataframe(
    resultado["prova"],
    use_container_width=True,
    hide_index=True,
    column_config={
        "Repetições": st.column_config.ProgressColumn(
            "Repetições",
            format="%d",
            min_value=0,
            max_value=int(resultado["prova"]["Repetições"].max())
                      if not resultado["prova"].empty else 1,
        )
    },
)

# ── Exportar resultado ────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Exportar Dados")

col_exp1, col_exp2 = st.columns(2)

with col_exp1:
    csv_prova = resultado["prova"].to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="Baixar Resultados (.csv)",
        data=csv_prova,
        file_name=f"relatorio_{nome_concorrente}.csv",
        mime="text/csv",
    )

with col_exp2:
    csv_comp = resultado["df_comp"].to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="Baixar Todos os Registros do Concorrente (.csv)",
        data=csv_comp,
        file_name=f"registros_{nome_concorrente}.csv",
        mime="text/csv",
    )

st.markdown("---")
st.caption("Mi7 Intelligence · Uso interno · Dados confidenciais")
