import pandas as pd
import json
import logging
from datetime import date as date_type

logger = logging.getLogger(__name__)


def _parse_single_bytes(json_bytes: bytes) -> pd.DataFrame:
    """
    Carrega bytes de UM único JSON e retorna um DataFrame Pandas,
    navegando recursivamente para achar a maior lista de registros.

    Distingue dois casos de dict com listas:
      - Formato colunar:  {"col1": [val1, val2], "col2": [val3, val4]}  → retorna o dict
      - Envelope/wrapper: {"pesquisas": [{...}, {...}]}                 → navega para dentro
    """
    try:
        data = json.loads(json_bytes.decode('utf-8'))

        def find_largest_list_of_dicts(node):
            best = []
            if isinstance(node, list):
                if len(node) > 0 and isinstance(node[0], dict):
                    best = node
                for item in node:
                    res = find_largest_list_of_dicts(item)
                    if len(res) > len(best):
                        best = res
            elif isinstance(node, dict):
                if all(isinstance(v, list) for v in node.values()):
                    lengths = {len(v) for v in node.values()}
                    if len(lengths) == 1:
                        # Colunar SOMENTE se as listas contêm escalares (não dicts)
                        # Ex. colunar: {"ean": ["A","B"], "preco": [1.0, 2.0]}
                        # Ex. envelope: {"pesquisas": [{"ean":"A"}, {"ean":"B"}]}
                        sample_items = [v[0] for v in node.values() if v]
                        if not any(isinstance(s, dict) for s in sample_items):
                            return node  # formato colunar — retorna o dict diretamente
                # Navega nos valores do dicionário
                for v in node.values():
                    res = find_largest_list_of_dicts(v)
                    if len(res) > len(best):
                        best = res
            return best

        extracted = find_largest_list_of_dicts(data)
        if not extracted:
            extracted = [data] if isinstance(data, dict) else data

        return pd.DataFrame(extracted)

    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"Arquivo JSON inválido ou corrompido: {e}")



def _load_dataframe(json_bytes_list) -> pd.DataFrame:
    """
    Aceita bytes de UM arquivo ou uma lista de bytes de MÚLTIPLOS arquivos.
    Concatena todos os registros em um único DataFrame antes da análise.
    """
    # Normaliza: sempre trabalha com lista
    if isinstance(json_bytes_list, bytes):
        json_bytes_list = [json_bytes_list]

    frames = []
    for idx, chunk in enumerate(json_bytes_list, start=1):
        try:
            frames.append(_parse_single_bytes(chunk))
        except ValueError as e:
            logger.warning('Arquivo %d ignorado: %s', idx, e)

    if not frames:
        raise ValueError('Nenhum arquivo JSON válido encontrado.')

    return pd.concat(frames, ignore_index=True)


def scan_competitors(json_bytes_list) -> list:
    """
    Escaneia um ou mais JSONs e retorna a lista de concorrentes detectados
    a partir da coluna 'observacao', excluindo registros Mi7.

    Aceita bytes de um único arquivo OU lista de bytes de múltiplos arquivos.
    Retorna lista de strings ordenada, ex: ['ClickSuper', 'InfoPrice'].
    """
    df = _load_dataframe(json_bytes_list)

    obs_col = next((c for c in df.columns if c.strip().lower() in ['observacao', 'obs']), None)
    if obs_col is None:
        raise ValueError("Coluna 'observacao' não encontrada no arquivo.")

    obs_upper = df[obs_col].astype(str).str.upper().str.strip()

    # Padrões que identificam Mi7 — excluímos eles
    mi7_mask = (
        obs_upper.str.contains('MENOR PREÇO', na=False) |
        obs_upper.str.contains('MENOR PRECO', na=False) |
        obs_upper.str.contains('ONLINE', na=False)
    )

    # Registros que NÃO são Mi7 — são concorrentes
    competitor_obs = obs_upper[~mi7_mask & obs_upper.notna() & (obs_upper != '') & (obs_upper != 'NAN')]

    # Extraímos palavras-chave únicas das observações
    # Cada observação pode ser um nome completo do concorrente
    competitors = sorted(competitor_obs.unique().tolist())

    # Filtra strings muito curtas ou genéricas
    competitors = [c for c in competitors if len(c) >= 3]

    return competitors[:50]  # limita a 50 para não sobrecarregar o front

def process_competition_analysis(
    json_bytes_list,
    competitor_name: str,
    date_from: date_type | None = None,
    date_to:   date_type | None = None,
) -> dict:
    """
    Processa um ou mais arquivos JSON de coleta de preços e gera métricas competitivas.

    Args:
        json_bytes_list: Bytes de UM arquivo OU lista de bytes de MÚLTIPLOS arquivos.
        competitor_name: Nome do concorrente a ser analisado (ex: 'ClickSuper').
        date_from:       Filtro de data inicial (inclusive). None = sem filtro.
        date_to:         Filtro de data final (inclusive). None = sem filtro.

    Returns:
        dict com todas as métricas e dados para o dashboard.

    Raises:
        ValueError: Se o JSON for inválido ou não tiver as colunas esperadas.
    """
    # --- 1. CARREGAMENTO (múltiplos arquivos concatenados) ---
    df = _load_dataframe(json_bytes_list)

    # --- 1b. FILTRO DE DATA ---
    # Detecta coluna de data (tolerante a variações de nome)
    date_col = next(
        (c for c in df.columns if c.strip().lower() in ['data', 'date', 'datacoleta', 'data_coleta']),
        None
    )
    if date_col and (date_from or date_to):
        df[date_col] = pd.to_datetime(df[date_col], errors='coerce', dayfirst=True)
        if date_from:
            df = df[df[date_col].dt.date >= date_from]
        if date_to:
            df = df[df[date_col].dt.date <= date_to]
        if df.empty:
            raise ValueError(
                f"Nenhum registro encontrado no intervalo "
                f"{date_from} → {date_to}. Verifique as datas selecionadas."
            )

    # --- 2. VALIDAÇÃO DE COLUNAS E CLASSIFICAÇÃO DE EMPRESA ---
    # Mapeamento flexível: aceita variações de nome de coluna
    column_mapping = _detect_columns(df)
    df = df.rename(columns=column_mapping)

    # Lógica customizada da Mi7 (baseado na coluna observacao)
    # Procuramos a coluna ignorando maiúsculas, minúsculas e espaços
    obs_col_match = next((c for c in df.columns if c.strip().lower() in ['observacao', 'obs']), None)
    
    if obs_col_match:
        df['observacao_limpa'] = df[obs_col_match].astype(str).str.upper()
        
        def classificar_empresa(obs):
            if 'MENOR PREÇO' in obs or 'MENOR PRECO' in obs or 'ONLINE' in obs:
                return 'Mi7'
            elif competitor_name.upper() in obs:
                return competitor_name
            else:
                return 'Outros'
                
        df['source'] = df['observacao_limpa'].apply(classificar_empresa)

    required = ['source', 'ean']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"Colunas necessárias não encontradas: {missing}. "
            f"Colunas disponíveis: {list(df.columns)}"
        )

    # --- 3. LIMPEZA ---
    df['ean'] = df['ean'].astype(str).str.strip().str.upper()
    df['source'] = df['source'].astype(str).str.strip()

    # Remove registros com EAN vazio ou nulo
    df = df[df['ean'].notna() & (df['ean'] != '') & (df['ean'] != 'NAN')]

    # --- 4. SEPARAÇÃO MI7 vs CONCORRENTE ---
    # Identifica registros da Mi7 (case-insensitive)
    mi7_mask = df['source'].str.contains('Mi7', case=False, na=False)
    competitor_mask = df['source'].str.contains(competitor_name, case=False, na=False)

    df_mi7 = df[mi7_mask].copy()
    df_competitor = df[competitor_mask].copy()

    if df_competitor.empty:
        sources_list = df['source'].unique().tolist()
        sources_sample = sources_list[:10] + ['...'] if len(sources_list) > 10 else sources_list
        raise ValueError(
            f"Nenhum registro encontrado para o concorrente '{competitor_name}'. "
            f"Coletores disponíveis no arquivo: {sources_sample}"
        )

    # --- 5. CÁLCULO DE MÉTRICAS ---

    # Mi7
    mi7_total = len(df_mi7)
    mi7_unique = df_mi7['ean'].nunique()

    # Concorrente
    comp_total = len(df_competitor)
    comp_unique = df_competitor['ean'].nunique()
    comp_duplicates = comp_total - comp_unique
    fake_data_index = round((comp_duplicates / comp_total * 100), 1) if comp_total > 0 else 0

    # --- 6. "PROVA DO CRIME" — Produtos mais repetidos pelo concorrente ---
    crime_table = (
        df_competitor
        .groupby('ean')
        .size()
        .reset_index(name='occurrences')
        .sort_values('occurrences', ascending=False)
    )

    # Adiciona nome do produto se a coluna existir
    if 'product_name' in df_competitor.columns:
        product_names = (
            df_competitor
            .groupby('ean')['product_name']
            .first()
            .reset_index()
        )
        crime_table = crime_table.merge(product_names, on='ean', how='left')
    else:
        crime_table['product_name'] = 'N/A'

    crime_table = crime_table.head(20)  # Top 20 mais repetidos

    # --- 7. DADOS PARA GRÁFICO ---
    chart_data = {
        'labels': ['Mi7', competitor_name],
        'total': [mi7_total, comp_total],
        'unique': [mi7_unique, comp_unique],
    }

    return {
        'competitor_name': competitor_name,
        'mi7': {
            'total': mi7_total,
            'unique': mi7_unique,
        },
        'competitor': {
            'total': comp_total,
            'unique': comp_unique,
            'duplicates': comp_duplicates,
            'fake_data_index': fake_data_index,
        },
        'crime_table': crime_table.to_dict(orient='records'),
        'chart_data': chart_data,
        # Só mostra as classes já classificadas (ex: Mi7, ClickSuper, Outros)
        'available_sources': sorted(df['source'].unique().tolist()),
    }

def _detect_columns(df: pd.DataFrame) -> dict:
    """
    Detecta variações de nomes de coluna no JSON e normaliza.
    Permite que o sistema funcione com JSONs de diferentes origens.
    """
    mapping = {}
    columns_lower = {col.lower(): col for col in df.columns}

    # Detectar coluna de fonte/coletor (se não vier pela observação)
    for candidate in ['source', 'coletor', 'collector', 'fonte', 'origem', 'company', 'codigoconcorrente']:
        if candidate in columns_lower:
            mapping[columns_lower[candidate]] = 'source'
            break

    # Detectar coluna de EAN/GTIN (usamos codigoProduto como identificador de itens únicos base)
    for candidate in ['codigoproduto', 'ean', 'gtin', 'barcode', 'codigo_barras', 'codigo', 'product_code', 'codbarras']:
        if candidate in columns_lower:
            mapping[columns_lower[candidate]] = 'ean'
            break

    # Detectar coluna de nome do produto
    for candidate in ['product_name', 'nome', 'name', 'descricao', 'description', 'produto']:
        if candidate in columns_lower:
            mapping[columns_lower[candidate]] = 'product_name'
            break

    return mapping
