import pandas as pd
import json
import io
from flask import current_app

def process_competition_analysis(json_bytes: bytes, competitor_name: str) -> dict:
    """
    Processa um arquivo JSON de coleta de preços e gera métricas competitivas.

    Args:
        json_bytes: Conteúdo do arquivo JSON em bytes (vindo do upload).
        competitor_name: Nome do concorrente a ser analisado (ex: 'ClickSuper').

    Returns:
        dict com todas as métricas e dados para o dashboard.

    Raises:
        ValueError: Se o JSON for inválido ou não tiver as colunas esperadas.
    """
    try:
        # --- 1. CARREGAMENTO ---
        # Lê o JSON de bytes para um DataFrame Pandas
        data = json.loads(json_bytes.decode('utf-8'))

        # Suporte para JSON como lista de objetos ou com chave raiz
        if isinstance(data, dict):
            # Tenta extrair a lista de registros de chaves comuns
            root_key = next(
                (k for k in ['data', 'items', 'records', 'results'] if k in data),
                None
            )
            data = data[root_key] if root_key else list(data.values())[0]

        df = pd.DataFrame(data)

    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"Arquivo JSON inválido ou corrompido: {e}")

    # --- 2. VALIDAÇÃO DE COLUNAS ---
    # Mapeamento flexível: aceita variações de nome de coluna
    column_mapping = _detect_columns(df)
    df = df.rename(columns=column_mapping)

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
    mi7_mask = df['source'].str.contains('mi7', case=False, na=False)
    competitor_mask = df['source'].str.contains(competitor_name, case=False, na=False)

    df_mi7 = df[mi7_mask].copy()
    df_competitor = df[competitor_mask].copy()

    if df_competitor.empty:
        raise ValueError(
            f"Nenhum registro encontrado para o concorrente '{competitor_name}'. "
            f"Coletores disponíveis no arquivo: {df['source'].unique().tolist()}"
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
        'available_sources': sorted(df['source'].unique().tolist()),
    }

def _detect_columns(df: pd.DataFrame) -> dict:
    """
    Detecta variações de nomes de coluna no JSON e normaliza.
    Permite que o sistema funcione com JSONs de diferentes origens.
    """
    mapping = {}
    columns_lower = {col.lower(): col for col in df.columns}

    # Detectar coluna de fonte/coletor
    for candidate in ['source', 'coletor', 'collector', 'fonte', 'origem', 'company']:
        if candidate in columns_lower:
            mapping[columns_lower[candidate]] = 'source'
            break

    # Detectar coluna de EAN/GTIN
    for candidate in ['ean', 'gtin', 'barcode', 'codigo_barras', 'codigo', 'product_code']:
        if candidate in columns_lower:
            mapping[columns_lower[candidate]] = 'ean'
            break

    # Detectar coluna de nome do produto
    for candidate in ['product_name', 'nome', 'name', 'descricao', 'description', 'produto']:
        if candidate in columns_lower:
            mapping[columns_lower[candidate]] = 'product_name'
            break

    return mapping
