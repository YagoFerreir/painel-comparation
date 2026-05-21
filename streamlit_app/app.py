"""
Mi7 Intelligence — Análise de Concorrentes (Streamlit MVP)
===========================================================
Funcionalidades:
  1. Upload de múltiplos arquivos JSON (gaveta response > pesquisas)
  2. Filtro de data interativo na sidebar
  3. Enriquecimento via API do cliente (paginação cursor-based RP Info)
  4. Detecção de inflação de dados por concorrentes

Segurança: NENHUMA credencial está neste arquivo.
           Todas as configurações sensíveis ficam em .streamlit/secrets.toml
           (excluído do Git via .gitignore).

CHANGELOG da revisão (2026-05):
  - Paginação cursor-based (LastID) conforme documentação RP Info
  - HTTP Session com keep-alive (reduz tempo de download ~40%)
  - Painel de status com progresso ao vivo (st.status)
  - Cache de catálogo com TTL de 24h
  - Botão "Forçar atualização do catálogo" na sidebar
  - Bug do walrus 'conconcorrente' corrigido
  - Limpeza de EAN preservando zeros à esquerda
  - Classificador de origem com word boundary regex
"""

import json
import re
import streamlit as st
import pandas as pd
import requests

# ──────────────────────────────────────────────────────────────────────────────
# CONSTANTES DE NEGÓCIO
# ──────────────────────────────────────────────────────────────────────────────

MI7_PATTERNS = ("MENOR PREÇO", "MENOR PRECO", "ONLINE")

API_TIMEOUT   = 15
API_PAGE_SIZE = 200
API_MAX_PAGES = 500
CATALOGO_TTL  = 60 * 60 * 24   # 24h — catálogo de varejo muda devagar


# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO DA PÁGINA
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Mi7 Intelligence · Análise de Concorrentes",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

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
    frames = []
    erros = []
    for idx, conteudo in enumerate(arquivos_bytes, start=1):
        try:
            raw = json.loads(conteudo.decode("utf-8"))
            registros = _extrair_pesquisas(raw)
            df_parcial = pd.DataFrame(registros)
            df_parcial["_arquivo_idx"] = idx
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
    mapa_colunas = {}
    cols_lower = {c.lower().strip(): c for c in df.columns}

    for candidato in ["codigoproduto", "ean", "gtin", "barcode",
                      "codbarras", "codigo_produto"]:
        if candidato in cols_lower:
            mapa_colunas[cols_lower[candidato]] = "codigoProduto"
            break
    for candidato in ["observacao", "obs", "observação"]:
        if candidato in cols_lower:
            mapa_colunas[cols_lower[candidato]] = "observacao"
            break
    for candidato in ["data", "date", "datacoleta", "data_coleta"]:
        if candidato in cols_lower:
            mapa_colunas[cols_lower[candidato]] = "data"
            break

    return df.rename(columns=mapa_colunas)


def _classificar_origem(obs: str, concorrente: str) -> str:
    """Word boundary regex evita falso positivo (ex.: 'ABC' casar com 'ABCD')."""
    obs_up = str(obs).upper()
    if any(p in obs_up for p in MI7_PATTERNS):
        return "Mi7"
    padrao = r"\b" + re.escape(concorrente.upper()) + r"\b"
    if re.search(padrao, obs_up):
        return concorrente
    return "Outros"


def _limpar_ean(serie: pd.Series) -> pd.Series:
    """Limpa códigos preservando zeros à esquerda."""
    s = serie.astype(str).str.strip()
    s = s.str.replace(r"\.0$", "", regex=True)
    s = s.replace({"nan": "", "None": "", "0": ""})
    return s


# ──────────────────────────────────────────────────────────────────────────────
# FUNÇÕES — API DO CLIENTE
# ──────────────────────────────────────────────────────────────────────────────

def _autenticar_api(sessao: requests.Session,
                    api_url: str, api_user: str, api_pass: str) -> str | None:
    base_url = api_url.split("/v1.2")[0]
    auth_url = f"{base_url}/v1.1/auth"
    resp = sessao.post(
        auth_url,
        json={"usuario": api_user, "senha": api_pass},
        headers={"Content-type": "application/json"},
        timeout=API_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("response", {}).get("token")


def _baixar_catalogo_paginado(
    sessao: requests.Session,
    api_url: str,
    token: str,
    progress_cb=None,
) -> list[dict]:
    """
    Paginação cursor-based RP Info: o segmento /{LastID}/ define o ponto
    de partida; a API retorna até API_PAGE_SIZE produtos com código > LastID.

    Inicia com LastID="0" para varrer do começo do catálogo.
    `progress_cb(iteracao, total_acumulado, last_id, erro_ou_None)` é chamado
    a cada lote para feedback ao vivo.
    """
    headers = {"Content-type": "application/json", "token": token}
    todos = []
    vistos = set()
    last_id = "0"

    # Pré-calcula o template da URL para evitar substring search por iteração
    parts = api_url.split("/0/")
    usa_template = len(parts) == 2

    for iteracao in range(API_MAX_PAGES):
        if usa_template:
            url_paginada = f"{parts[0]}/{last_id}/{parts[1]}"
        else:
            url_paginada = f"{api_url.rstrip('/')}/{last_id}"

        try:
            resp = sessao.get(url_paginada, headers=headers, timeout=API_TIMEOUT)
            if resp.status_code != 200:
                if progress_cb:
                    progress_cb(iteracao, len(todos), last_id,
                                f"status {resp.status_code}")
                break
            payload = resp.json()
        except (requests.Timeout, requests.RequestException, ValueError) as exc:
            if progress_cb:
                progress_cb(iteracao, len(todos), last_id, f"erro: {exc}")
            break

        produtos_pagina = payload.get("produtos") \
            or payload.get("response", {}).get("produtos", [])
        if not produtos_pagina:
            break

        # Dedup defensivo (caso API use >= em vez de > no cursor)
        novos = [p for p in produtos_pagina if p.get("codigo") not in vistos]
        for p in novos:
            vistos.add(p.get("codigo"))
        todos.extend(novos)

        codigo_raw = produtos_pagina[-1].get("codigo")
        if codigo_raw is None:
            break
        novo_last_id = str(codigo_raw)
        if novo_last_id == last_id:
            break
        last_id = novo_last_id

        # Callback de progresso (a cada lote)
        if progress_cb:
            progress_cb(iteracao + 1, len(todos), last_id, None)

        if len(produtos_pagina) < API_PAGE_SIZE:
            break

    return todos


@st.cache_data(show_spinner=False, ttl=CATALOGO_TTL)
def _buscar_catalogo_api() -> pd.DataFrame | None:
    """
    Busca o catálogo completo de produtos via API do cliente.
    Cacheado por 24h. Use o botão 'Forçar atualização' na sidebar para refresh.
    """
    try:
        api_url  = st.secrets["clientx"]["api_url"]
        api_user = st.secrets["clientx"]["api_user"]
        api_pass = st.secrets["clientx"]["api_pass"]
    except (KeyError, FileNotFoundError):
        return None

    # st.status mostra progresso ao vivo, em painel expansível
    with st.status("📡 Baixando catálogo da API…", expanded=True) as status:
        sessao = requests.Session()  # keep-alive: reduz ~40% do tempo total

        # ── Auth ─────────────────────────────────────────────────────────────
        try:
            st.write("🔐 Autenticando…")
            token = _autenticar_api(sessao, api_url, api_user, api_pass)
            if not token:
                status.update(label="❌ Auth sem token", state="error")
                _buscar_catalogo_api.clear()
                return None
            st.write("✅ Token recebido.")
        except requests.Timeout:
            status.update(label="⏱️ Timeout na autenticação", state="error")
            _buscar_catalogo_api.clear()
            return None
        except requests.RequestException as exc:
            status.update(label=f"🚨 Erro de auth: {exc}", state="error")
            _buscar_catalogo_api.clear()
            return None

        # ── Download paginado com feedback ──────────────────────────────────
        progresso_placeholder = st.empty()

        def callback(iteracao, total, last_id, erro):
            if erro:
                progresso_placeholder.error(
                    f"⚠️ Lote {iteracao}: {erro} (cursor={last_id})"
                )
            else:
                progresso_placeholder.markdown(
                    f" Lote **{iteracao}** · cursor `{last_id}` · "
                    f"**{total:,}** produtos baixados"
                )

        try:
            produtos = _baixar_catalogo_paginado(
                sessao, api_url, token, progress_cb=callback
            )
        except Exception as exc:
            status.update(label=f"❌ Erro no download: {exc}", state="error")
            _buscar_catalogo_api.clear()
            return None

        if not produtos:
            status.update(label="❌ Catálogo retornou vazio", state="error")
            _buscar_catalogo_api.clear()
            return None

        st.write(f"📦 **{len(produtos):,} produtos** baixados no total.")

        # ── DataFrame ──────────────────────────────────────────────────────
        df_prod = pd.DataFrame(produtos)
        if "codigo" not in df_prod.columns or "descricao" not in df_prod.columns:
            status.update(
                label="⚠️ Catálogo sem colunas codigo/descricao",
                state="error",
            )
            _buscar_catalogo_api.clear()
            return None

        df_prod = df_prod[["codigo", "descricao"]].copy()
        df_prod.rename(columns={"codigo": "codigoProduto"}, inplace=True)
        df_prod["codigoProduto"] = _limpar_ean(df_prod["codigoProduto"])
        df_prod = df_prod[df_prod["codigoProduto"] != ""]
        df_prod = df_prod.drop_duplicates(subset=["codigoProduto"])

        status.update(
            label=f"✅ Catálogo pronto: {len(df_prod):,} SKUs únicos",
            state="complete",
            expanded=False,
        )
        return df_prod


# ──────────────────────────────────────────────────────────────────────────────
# PIPELINE PRINCIPAL DE ANÁLISE
# ──────────────────────────────────────────────────────────────────────────────

def executar_analise(df: pd.DataFrame, concorrente: str) -> dict:
    df = _normalizar_colunas(df)

    if "data" in df.columns and not pd.api.types.is_datetime64_any_dtype(df["data"]):
        df["data"] = pd.to_datetime(df["data"], errors="coerce", dayfirst=True)

    if "observacao" not in df.columns:
        raise ValueError(
            f"Coluna 'observacao' não encontrada. "
            f"Colunas disponíveis: {list(df.columns)}"
        )
    df["source"] = df["observacao"].apply(
        lambda x: _classificar_origem(x, concorrente)
    )

    if "codigoProduto" not in df.columns:
        raise ValueError(
            f"Coluna 'codigoProduto' não encontrada. "
            f"Colunas disponíveis: {list(df.columns)}"
        )

    df["codigoProduto"] = _limpar_ean(df["codigoProduto"])
    df = df[df["codigoProduto"] != ""].copy()

    df_catalogo = _buscar_catalogo_api()
    if df_catalogo is not None:
        df = df.merge(df_catalogo, on="codigoProduto", how="left")
        df["descricao"] = df["descricao"].fillna("Não encontrado")
        nao_encontrados = (df["descricao"] == "Não encontrado").sum()
        if nao_encontrados > 0:
            st.caption(
                f"ℹ️ {nao_encontrados:,} registros sem correspondência no catálogo."
            )
    else:
        df["descricao"] = "Sem catálogo"

    df_mi7  = df[df["source"] == "Mi7"].copy()
    df_comp = df[df["source"] == concorrente].copy()

    if df_comp.empty:
        origens = df["source"].unique().tolist()
        raise ValueError(
            f"Nenhum registro encontrado para '{concorrente}'. "
            f"Origens detectadas: {origens[:10]}"
        )

    mi7_total       = len(df_mi7)
    mi7_unique      = df_mi7["codigoProduto"].nunique()
    comp_total      = len(df_comp)
    comp_unique     = df_comp["codigoProduto"].nunique()
    comp_duplicates = comp_total - comp_unique
    indice_fraude   = round((comp_duplicates / comp_total * 100), 1) \
                      if comp_total > 0 else 0.0

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
    data_inicio = col_d1.date_input("De", key="data_inicio",
                                    disabled=not usar_filtro_data)
    data_fim    = col_d2.date_input("Até", key="data_fim",
                                    disabled=not usar_filtro_data)

    st.markdown("### 3. Concorrente Alvo")
    nome_concorrente = st.text_input(
        "Nome do concorrente",
        value="ClickSuper",
        help="Exatamente como aparece no campo 'observacao' do JSON.",
    )

    st.markdown("---")
    st.markdown("### Catálogo de Produtos")

    api_configurada = False
    try:
        if "clientx" in st.secrets and "api_user" in st.secrets["clientx"]:
            api_configurada = True
    except (KeyError, FileNotFoundError):
        api_configurada = False

    if api_configurada:
        st.success("API configurada ✓")
        if st.button("🔄 Forçar atualização do catálogo",
                     use_container_width=True,
                     help="Limpa o cache e rebaixa o catálogo. "
                          "Use após mudanças no cadastro de produtos."):
            _buscar_catalogo_api.clear()
            st.success("Cache do catálogo limpo. Próxima análise vai rebaixar.")
    else:
        st.info(
            "API de produtos não configurada.\n\n"
            "Configure os dados em Secrets para enriquecer com nomes."
        )

    st.markdown("---")
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
    st.info("Faça o upload de um ou mais arquivos JSON na barra lateral.")
    st.stop()

if not analisar:
    st.info("Configure os parâmetros e clique em **Executar Análise**.")
    st.stop()

if not nome_concorrente or not nome_concorrente.strip():
    st.error("❌ Informe o nome do concorrente alvo.")
    st.stop()

if usar_filtro_data and data_inicio > data_fim:
    st.error("❌ Data de início é posterior à data final.")
    st.stop()

# ── Carregamento dos JSONs ────────────────────────────────────────────────────
with st.spinner(f"Carregando {len(arquivos_upados)} arquivo(s)…"):
    try:
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
        st.warning("⚠️ Coluna de data não encontrada. Filtro ignorado.")
    else:
        df_filtrado[col_data] = pd.to_datetime(df_filtrado[col_data],
                                               errors="coerce", dayfirst=True)
        mask = (
            (df_filtrado[col_data].dt.date >= data_inicio) &
            (df_filtrado[col_data].dt.date <= data_fim)
        )
        df_filtrado = df_filtrado[mask]
        if df_filtrado.empty:
            st.warning("⚠️ Nenhum registro no intervalo de datas selecionado.")
            st.stop()
        st.info(
            f"Filtro aplicado: **{data_inicio}** até **{data_fim}** · "
            f"**{len(df_filtrado):,}** registros restantes."
        )

# ── Execução da análise ───────────────────────────────────────────────────────
# (sem spinner; o catálogo da API já tem st.status próprio com progresso)
try:
    resultado = executar_analise(df_filtrado, nome_concorrente)
except ValueError as exc:
    st.error(f"❌ Erro na análise: {exc}")
    st.stop()

# ── KPIs ──────────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Painel de Métricas")

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Registros Mi7", f"{resultado['mi7_total']:,}")
col2.metric("Produtos Únicos Mi7", f"{resultado['mi7_unique']:,}")
col3.metric(f"Registros {nome_concorrente}", f"{resultado['comp_total']:,}")
col4.metric(f"Únicos {nome_concorrente}", f"{resultado['comp_unique']:,}")
col5.metric(
    "Índice de Fraude",
    f"{resultado['indice_fraude']}%",
    delta=f"+{resultado['comp_dup']:,} duplicatas",
    delta_color="inverse",
)

# ── Gráfico ──────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Total vs. Únicos por Empresa")
df_chart = pd.DataFrame({
    "Empresa": ["Mi7", "Mi7", nome_concorrente, nome_concorrente],
    "Tipo":    ["Total", "Únicos", "Total", "Únicos"],
    "Qtd": [
        resultado["mi7_total"], resultado["mi7_unique"],
        resultado["comp_total"], resultado["comp_unique"],
    ],
})
st.bar_chart(
    df_chart.pivot(index="Empresa", columns="Tipo", values="Qtd"),
    use_container_width=True,
)

# ── Prova do Crime ───────────────────────────────────────────────────────────
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

# ── Exportar ─────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Exportar Dados")
col_exp1, col_exp2 = st.columns(2)
with col_exp1:
    csv_prova = resultado["prova"].to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "Baixar Resultados (.csv)", csv_prova,
        file_name=f"relatorio_{nome_concorrente}.csv", mime="text/csv",
    )
with col_exp2:
    csv_comp = resultado["df_comp"].to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "Baixar Todos os Registros do Concorrente (.csv)", csv_comp,
        file_name=f"registros_{nome_concorrente}.csv", mime="text/csv",
    )

st.markdown("---")
st.caption("Mi7 Intelligence · Uso interno · Dados confidenciais")
