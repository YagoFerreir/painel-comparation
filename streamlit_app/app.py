import json
import io
import streamlit as st
import pandas as pd
import requests

# 1. CONFIGURAÇÃO DA PÁGINA (Padrão do Streamlit, sem CSS customizado)
st.set_page_config(
    page_title="Mi7 Intelligence · Análise de Concorrentes",
    page_icon="🔍",
    layout="wide"
)

MI7_PATTERNS = ("MENOR PREÇO", "MENOR PRECO", "ONLINE")
API_TIMEOUT = 15

# 2. FUNÇÕES DE CARREGAMENTO E PARSING
def _extrair_pesquisas(raw: dict) -> list[dict]:
    try:
        pesquisas = raw["response"]["pesquisas"]
        if not isinstance(pesquisas, list):
            raise ValueError("O campo 'pesquisas' não é uma lista.")
        return pesquisas
    except KeyError as exc:
        chave_faltando = str(exc).strip("'")
        raise ValueError(f"Estrutura JSON inválida: chave '{chave_faltando}' não encontrada.") from exc

@st.cache_data(show_spinner=False)
def carregar_multiplos_jsons(arquivos_bytes: list[bytes]) -> pd.DataFrame:
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
    for candidato in ["codigoproduto", "ean", "gtin", "barcode", "codbarras", "codigo_produto"]:
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
    obs_up = str(obs).upper()
    if any(p in obs_up for p in MI7_PATTERNS):
        return "Mi7"
    if concorrente.upper() in obs_up:
        return concorrente
    return "Outros"

@st.cache_data(show_spinner="Buscando catálogo de produtos na API…", ttl=3600)
def _buscar_catalogo_api() -> pd.DataFrame | None:
    # Correção da leitura segura de secrets
    try:
        if "irani" in st.secrets and "api_url" in st.secrets["irani"] and "api_token" in st.secrets["irani"]:
            api_url = st.secrets["irani"]["api_url"]
            api_token = st.secrets["irani"]["api_token"]
        else:
            return None
    except Exception:
        return None

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        resp = requests.get(api_url, headers=headers, timeout=API_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        produtos = payload.get("produtos", [])
        if not produtos:
            return None
        df_prod = pd.DataFrame(produtos)[["codigo", "descricao"]].copy()
        df_prod.rename(columns={"codigo": "codigoProduto"}, inplace=True)
        df_prod["codigoProduto"] = df_prod["codigoProduto"].astype(str).str.strip()
        return df_prod
    except Exception:
        return None

def executar_analise(df: pd.DataFrame, concorrente: str) -> dict:
    df = _normalizar_colunas(df)
    if "data" in df.columns:
        df["data"] = pd.to_datetime(df["data"], errors="coerce", dayfirst=True)
    if "observacao" not in df.columns:
        raise ValueError("Coluna 'observacao' não encontrada.")
    df["source"] = df["observacao"].apply(lambda x: _classificar_origem(x, concorrente))
    if "codigoProduto" not in df.columns:
        raise ValueError("Coluna 'codigoProduto' não encontrada.")
    df["codigoProduto"] = df["codigoProduto"].astype(str).str.strip()
    df = df[df["codigoProduto"].notna() & (df["codigoProduto"] != "") & (df["codigoProduto"] != "nan")]

    df_catalogo = _buscar_catalogo_api()
    if df_catalogo is not None:
        df = df.merge(df_catalogo, on="codigoProduto", how="left")
        df["descricao"] = df["descricao"].fillna("Não encontrado")
    else:
        df["descricao"] = "Sem catálogo"

    df_mi7 = df[df["source"].str.contains("Mi7", case=False, na=False)].copy()
    df_comp = df[df["source"].str.contains(concorrente, case=False, na=False)].copy()

    if df_comp.empty:
        raise ValueError(f"Nenhum registro encontrado para '{concorrente}'.")

    mi7_total = len(df_mi7)
    mi7_unique = df_mi7["codigoProduto"].nunique()
    comp_total = len(df_comp)
    comp_unique = df_comp["codigoProduto"].nunique()
    comp_duplicates = comp_total - comp_unique
    indice_fraude = round((comp_duplicates / comp_total * 100), 1) if comp_total > 0 else 0.0

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
        "df_completo": df, "df_mi7": df_mi7, "df_comp": df_comp, "prova": prova,
        "mi7_total": mi7_total, "mi7_unique": mi7_unique, "comp_total": comp_total,
        "comp_unique": comp_unique, "comp_dup": comp_duplicates, "indice_fraude": indice_fraude,
        "concorrente": concorrente
    }

# 3. INTERFACE — SIDEBAR
with st.sidebar:
    st.markdown("## 🔍 Mi7 Intelligence")
    st.markdown("---")
    arquivos_upados = st.file_uploader("📂 Selecione os arquivos JSON", type=["json"], accept_multiple_files=True)
    
    usar_filtro_data = st.checkbox("📅 Ativar filtro de data", value=False)
    col_d1, col_d2 = st.columns(2)
    data_inicio = col_d1.date_input("De", disabled=not usar_filtro_data)
    data_fim = col_d2.date_input("Até", disabled=not usar_filtro_data)

    nome_concorrente = st.text_input("🏢 Nome do concorrente", value="ClickSuper")

# 4. INTERFACE — ÁREA PRINCIPAL (Fluxo contínuo e automático)
st.title("🔍 Mi7 Intelligence — Análise de Concorrentes")
st.caption("Ferramenta interna para detecção de inflação de dados por coletores terceiros.")

if not arquivos_upados:
    st.info("👈 Faça o upload de um ou mais arquivos JSON na barra lateral para iniciar.", icon="📂")
    st.stop()

# Executa automaticamente ao carregar os arquivos (Removeu o botão travando)
with st.spinner("Processando arquivos…"):
    try:
        lista_bytes = [f.read() for f in arquivos_upados]
        df_bruto = carregar_multiplos_jsons(lista_bytes)
    except ValueError as exc:
        st.error(f"❌ Erro: {exc}")
        st.stop()

df_filtrado = df_bruto.copy()
if usar_filtro_data:
    col_data = next((c for c in df_filtrado.columns if c.lower().strip() in ["data", "date", "datacoleta", "data_coleta"]), None)
    if col_data is not None:
        df_filtrado[col_data] = pd.to_datetime(df_filtrado[col_data], errors="coerce", dayfirst=True)
        mask = (df_filtrado[col_data].dt.date >= data_inicio) & (df_filtrado[col_data].dt.date <= data_fim)
        df_filtrado = df_filtrado[mask]

with st.spinner("Executando análise competitiva…"):
    try:
        resultado = executar_analise(df_filtrado, nome_concorrente)
    except ValueError as exc:
        st.error(f"❌ Erro na análise: {exc}")
        st.stop()

# Exibição nativa estável do Streamlit
st.markdown("---")
st.subheader("📊 Painel de Métricas")
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Registros Mi7", f"{resultado['mi7_total']:,}")
col2.metric("Produtos Únicos Mi7", f"{resultado['mi7_unique']:,}")
col3.metric(f"Registros {nome_concorrente}", f"{resultado['comp_total']:,}")
col4.metric(f"Únicos {nome_concorrente}", f"{resultado['comp_unique']:,}")
col5.metric("🚨 Índice de Fraude", f"{resultado['indice_fraude']}%", delta=f"+{resultado['comp_dup']:,} duplicatas", delta_color="inverse")

st.markdown("---")
df_chart = pd.DataFrame({
    "Empresa": ["Mi7", "Mi7", nome_concorrente, nome_concorrente],
    "Tipo": ["Total", "Únicos", "Total", "Únicos"],
    "Qtd": [resultado["mi7_total"], resultado["mi7_unique"], resultado["comp_total"], resultado["comp_unique"]]
})
st.bar_chart(df_chart.pivot(index="Empresa", columns="Tipo", values="Qtd"), use_container_width=True)

st.markdown("---")
st.subheader("🚨 Prova do Crime — Top 20 Produtos Mais Repetidos")
st.dataframe(resultado["prova"], use_container_width=True, hide_index=True)
