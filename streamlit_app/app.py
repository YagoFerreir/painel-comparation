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
"""

import json
import io
import streamlit as st
import pandas as pd
import requests
import concurrent.futures
# ──────────────────────────────────────────────────────────────────────────────
# CONSTANTES DE NEGÓCIO
# ──────────────────────────────────────────────────────────────────────────────

# Padrões de observação que identificam coletas da própria Mi7
MI7_PATTERNS = ("MENOR PREÇO", "MENOR PRECO", "ONLINE")

# Timeout (segundos) para chamada da API do cliente
API_TIMEOUT = 15


# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO DA PÁGINA
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Mi7 Intelligence · Análise de Concorrentes",
    page_icon="",
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
        pesquisas = raw["response"]["pesquisas"]
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
def carregar_multiplos_jsons(arquivos_bytes: list[bytes]) -> pd.DataFrame:
    """
    Recebe uma lista de bytes (um por arquivo) e retorna um DataFrame
    concatenado com todos os registros de 'pesquisas'.
    Usa st.cache_data para não reprocessar arquivos já carregados.
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
        # Exibe avisos sem expor estrutura interna da API
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
    for candidato in ["codigoproduto", "ean", "gtin", "barcode", "codbarras", "codigo_produto"]:
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
    """Classifica cada registro como Mi7, concorrente selecionado ou Outros."""
    obs_up = str(obs).upper()
    if any(p in obs_up for p in MI7_PATTERNS):
        return "Mi7"
    if concorrente.upper() in obs_up:
        return concorrente
    return "Outros"


# ──────────────────────────────────────────────────────────────────────────────
# FUNÇÃO AUXILIAR — API DO CLIENTE (SEGURA)
# ──────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner="Baixando catálogo completo (são várias páginas, leva uns 30 seg na 1ª vez)...", ttl=3600)
def _buscar_catalogo_api() -> pd.DataFrame | None:
    try:
        api_url  = st.secrets["clientx"]["api_url"]
        api_user = st.secrets["clientx"]["api_user"]
        api_pass = st.secrets["clientx"]["api_pass"]
    except Exception:
        return None

    base_url = api_url.split("/v1.2")[0]
    auth_url = f"{base_url}/v1.1/auth"
    
    try:
        # --- 1. PEGA O TOKEN ---
        resp_auth = requests.post(
            auth_url, 
            json={"usuario": api_user, "senha": api_pass}, 
            headers={"Content-type": "application/json"}, 
            timeout=API_TIMEOUT
        )
        resp_auth.raise_for_status()
        token = resp_auth.json().get("response", {}).get("token")
        
        if not token:
            st.error("🚨 Login feito, mas sem token retornado.")
            return None

        # --- 2. MULTI-THREADING PARA BAIXAR TUDO RÁPIDO ---
        headers_produtos = {"Content-type": "application/json", "token": token}
        todos_produtos = []

        # Função auxiliar que os "trabalhadores" vão usar para pegar 1 página
        def fetch_page(pagina):
            url_paginada = api_url.replace("/0/", f"/{pagina}/")
            try:
                resp = requests.get(url_paginada, headers=headers_produtos, timeout=10)
                if resp.status_code == 200:
                    payload = resp.json()
                    prods = payload.get("produtos", [])
                    if not prods and "response" in payload:
                        prods = payload.get("response", {}).get("produtos", [])
                    return prods
            except Exception:
                return []
            return []

        # Dispara 20 requisições simultâneas. Tenta puxar até a página 200 (40.000 produtos)
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            resultados = list(executor.map(fetch_page, range(200)))

        # Junta todas as páginas que voltaram com dados
        for prods in resultados:
            if prods:
                todos_produtos.extend(prods)

        # --- 3. FINALIZA O DATAFRAME ---
        if todos_produtos:
            df_prod = pd.DataFrame(todos_produtos)[["codigo", "descricao"]].copy()
            df_prod.rename(columns={"codigo": "codigoProduto"}, inplace=True)
            df_prod["codigoProduto"] = df_prod["codigoProduto"].astype(str).str.strip()
            # Remove duplicatas caso a API retorne páginas repetidas
            return df_prod.drop_duplicates(subset=["codigoProduto"])
        else:
            st.info("Catálogo de produtos retornou vazio.")
            return None

    except Exception as e:
        st.error(f"🚨 ERRO NA API: {e}")
        return None

        # --- 2. LOOP DE PAGINAÇÃO PARA BAIXAR TUDO ---
        headers_produtos = {"Content-type": "application/json", "token": token}
        todos_produtos = []
        pagina = 0
        
        while True:
            # Substitui o "0" da URL original pela página atual do loop
            url_paginada = api_url.replace("/0/", f"/{pagina}/")
            
            resp_prod = requests.get(url_paginada, headers=headers_produtos, timeout=API_TIMEOUT)
            
            # Se a API der erro ou parar de responder, interrompe o loop
            if resp_prod.status_code != 200:
                break
                
            payload = resp_prod.json()
            
            # Extrai os produtos da gaveta raiz ou da gaveta response
            produtos_pagina = payload.get("produtos", [])
            if not produtos_pagina and "response" in payload:
                produtos_pagina = payload.get("response", {}).get("produtos", [])
            
            # Se a página vier vazia, significa que o catálogo acabou!
            if not produtos_pagina:
                break
                
            todos_produtos.extend(produtos_pagina)
            
            # A API entrega de 200 em 200. Se vier menos que isso, é a última página.
            if len(produtos_pagina) < 200:
                break
                
            pagina += 1
            
            # Trava de segurança para evitar loops infinitos (ex: max 500 páginas = 100.000 produtos)
            if pagina > 500:
                break

        # --- 3. FINALIZA O DATAFRAME ---
        if todos_produtos:
            df_prod = pd.DataFrame(todos_produtos)[["codigo", "descricao"]].copy()
            df_prod.rename(columns={"codigo": "codigoProduto"}, inplace=True)
            df_prod["codigoProduto"] = df_prod["codigoProduto"].astype(str).str.strip()
            return df_prod
        else:
            st.info("Catálogo de produtos retornou vazio.")
            return None

    except Exception as e:
        st.error(f"🚨 ERRO NA API: {e}")
        return None
        # --- ETAPA 2: BUSCAR OS PRODUTOS COM O TOKEN ---
        # Exatamente como o manual do clientx pediu: "enviando o atributo 'token'"
        headers_produtos = {
            "Content-type": "application/json",
            "token": token
        }

        # Bate na porta de produtos agora com a permissão
        resp_prod = requests.get(api_url, headers=headers_produtos, timeout=API_TIMEOUT)
        resp_prod.raise_for_status()
        payload = resp_prod.json()

        produtos = payload.get("produtos", [])
        if not produtos and "response" in payload:
            produtos = payload.get("response", {}).get("produtos", [])
          
        if produtos:
            df_prod = pd.DataFrame(produtos)[["codigo", "descricao"]].copy()
            df_prod.rename(columns={"codigo": "codigoProduto"}, inplace=True)
            df_prod["codigoProduto"] = df_prod["codigoProduto"].astype(str).str.strip()
            return df_prod
        else:
            st.info("Catálogo de produtos retornou vazio. Os EANs serão exibidos sem nome.")
            return None

    except Exception as e:
        # Se algo falhar (senha errada, firewall, etc), vai mostrar o erro vermelho na tela
        st.error(f"🚨 ERRO NA API: {e}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# PIPELINE PRINCIPAL DE ANÁLISE
# ──────────────────────────────────────────────────────────────────────────────

def executar_analise(df: pd.DataFrame, concorrente: str) -> dict:
    """
    Pipeline completo:
      1. Normaliza colunas
      2. Converte datas
      3. Classifica origem (Mi7 vs concorrente)
      4. Limpeza de EANs
      5. Merge com catálogo de produtos (se disponível)
      6. Calcula métricas e tabela 'Prova do Crime'
    """
    df = _normalizar_colunas(df)

    # Converte coluna de data para datetime (tolerante a erros)
    if "data" in df.columns:
        df["data"] = pd.to_datetime(df["data"], errors="coerce", dayfirst=True)

    # Classifica origem
    if "observacao" not in df.columns:
        raise ValueError(
            "Coluna 'observacao' não encontrada. "
            f"Colunas disponíveis: {list(df.columns)}"
        )
    df["source"] = df["observacao"].apply(lambda x: _classificar_origem(x, concorrente))

    # Limpa EANs
    if "codigoProduto" not in df.columns:
        raise ValueError(
            "Coluna 'codigoProduto' (ou equivalente) não encontrada. "
            f"Colunas disponíveis: {list(df.columns)}"
        )
    df["codigoProduto"] = df["codigoProduto"].astype(str).str.strip()
    df = df[df["codigoProduto"].notna() & (df["codigoProduto"] != "") & (df["codigoProduto"] != "nan")]

    
    # ── Merge com catálogo de produtos (PROCV) ───────────────────────────────
    df_catalogo = _buscar_catalogo_api()
    if df_catalogo is not None:
        df = df.merge(df_catalogo, on="codigoProduto", how="left")
        df["descricao"] = df["descricao"].fillna("Não encontrado")
    else:
        df["descricao"] = "Sem catálogo"

    # ── Separação Mi7 vs Concorrente ─────────────────────────────────────────
    df_mi7  = df[df["source"].str.contains("Mi7", case=False, na=False)].copy()
    df_comp = df[df["source"].str.contains(concorrente, case=False, na=False)].copy()

    if df_comp.empty:
        origens = df["source"].unique().tolist()
        raise ValueError(
            f"Nenhum registro encontrado para '{concorrente}'. "
            f"Origens no arquivo: {origens[:10]}"
        )

    # ── Métricas ─────────────────────────────────────────────────────────────
    mi7_total     = len(df_mi7)
    mi7_unique    = df_mi7["codigoProduto"].nunique()

    comp_total      = len(df_comp)
    comp_unique     = df_comp["codigoProduto"].nunique()
    comp_duplicates = comp_total - comp_unique
    indice_fraude   = round((comp_duplicates / comp_total * 100), 1) if comp_total > 0 else 0.0

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

    # Os seletores de data são desabilitados até o filtro ser ativado
    col_d1, col_d2 = st.columns(2)
    data_inicio = col_d1.date_input("De", key="data_inicio", disabled=not usar_filtro_data)
    data_fim    = col_d2.date_input("Até", key="data_fim", disabled=not usar_filtro_data)

    st.markdown("### 3. Concorrente Alvo")
    nome_concorrente = st.text_input(
        "Nome do concorrente",
        value="ClickSuper",
        help="Exatamente como aparece no campo 'observacao' do JSON.",
    )

    st.markdown("---")

    # Bloco robusto de validação de segredos
   # Bloco robusto de validação de segredos
    api_configurada = False
    try:
        if "clientx" in st.secrets and "api_user" in st.secrets["clientx"]:
            api_configurada = True
    except Exception:
        api_configurada = False

    if api_configurada:
        st.success("API de produtos configurada.")
    else:
        st.info(
            "API de produtos não configurada.\n\n"
            "Configure os dados em Secrets para enriquecer com nomes."
        )

    analisar = st.button("Executar Análise", type="primary", use_container_width=True)


# ──────────────────────────────────────────────────────────────────────────────
# INTERFACE — ÁREA PRINCIPAL
# ──────────────────────────────────────────────────────────────────────────────

st.title("Mi7 Intelligence — Análise de Concorrentes")
st.caption("Ferramenta interna para detecção de inflação de dados por coletores terceiros.")

if not arquivos_upados:
    st.info(
        "Faça o upload de um ou mais arquivos JSON na barra lateral para iniciar."
    )
    st.stop()

if not analisar:
    st.info("Configure os parâmetros na barra lateral e clique em **▶ Executar Análise**.")
    st.stop()

# ── Carregamento e concatenação ───────────────────────────────────────────────
with st.spinner(f"Carregando {len(arquivos_upados)} arquivo(s)…"):
    try:
        lista_bytes = [f.read() for f in arquivos_upados]
        df_bruto = carregar_multiplos_jsons(lista_bytes)
    except ValueError as exc:
        st.error(f"❌ Erro no carregamento: {exc}")
        st.stop()

st.success(
    f" {len(arquivos_upados)} arquivo(s) carregado(s) · "
    f"**{len(df_bruto):,}** registros totais antes do filtro.")

# ── Filtro de data ────────────────────────────────────────────────────────────
df_filtrado = df_bruto.copy()

if usar_filtro_data:
    col_data = next(
        (c for c in df_filtrado.columns if c.lower().strip() in ["data", "date", "datacoleta", "data_coleta"]),
        None,
    )
    if col_data is None:
        st.warning("⚠️ Coluna de data não encontrada no JSON. O filtro de data não foi aplicado.")
    else:
        df_filtrado[col_data] = pd.to_datetime(df_filtrado[col_data], errors="coerce", dayfirst=True)
        mask = (
            (df_filtrado[col_data].dt.date >= data_inicio) &
            (df_filtrado[col_data].dt.date <= data_fim)
        )
        df_filtrado = df_filtrado[mask]

        if df_filtrado.empty:
            st.warning("⚠️ Nenhum registro encontrado no intervalo de datas selecionado.")
            st.stop()

        st.info(
            f"Filtro applied: **{data_inicio}** até **{data_fim}** · "
            f"**{len(df_filtrado):,}** registros restantes.")

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
            max_value=int(resultado["prova"]["Repetições"].max()) if not resultado["prova"].empty else 1,
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
