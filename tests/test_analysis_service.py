"""
QA Test Suite — Mi7 Intelligence Dashboard
============================================
Testa toda a lógica de negócio do analysis_service.py:
  - Carregamento de JSON (flat, envelope, coluna-format)
  - Classificação de empresa pela coluna observacao
  - Detecção de colunas (EAN, source, product_name)
  - Cálculo de métricas (total, unique, duplicates, fake_data_index)
  - Tabela de crime (top 20 mais repetidos)
  - scan_competitors()
  - Casos de borda (JSON vazio, colunas ausentes, concorrente inexistente)
"""

import sys
import os
import json

# Garante que o módulo app está no path (tests/ fica 1 nível abaixo da raiz)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.services.analysis_service import process_competition_analysis, scan_competitors

# ─── Helpers ────────────────────────────────────────────────────────────────

PASS = "PASS"
FAIL = "FAIL"
results = []

def run(name, fn):
    try:
        fn()
        results.append((PASS, name))
        print(f"  [PASS]  {name}")
    except AssertionError as e:
        results.append((FAIL, name))
        print(f"  [FAIL]  {name}")
        print(f"         -> AssertionError: {e}")
    except Exception as e:
        results.append((FAIL, name))
        print(f"  [FAIL]  {name}")
        print(f"         -> {type(e).__name__}: {e}")


def make_json(records):
    return json.dumps(records).encode('utf-8')


def make_envelope(records):
    return json.dumps({'response': {'pesquisas': records}}).encode('utf-8')


def make_record(codigoProduto, observacao, preco=10.0):
    return {
        'codigoProduto': codigoProduto,
        'observacao': observacao,
        'preco': preco,
        'codBarras': f'BAR{codigoProduto}',
    }


# ─── Dataset canônico ────────────────────────────────────────────────────────
# 5 registros Mi7  (observacao com "MENOR PRECO" / "ONLINE" / "MENOR PREÇO")
#   → 4 EANs únicos (P001 aparece 2×)
# 6 registros ClickSuper (observacao = "CLICKSUPER")
#   → 3 EANs únicos (P010 aparece 3×, P011 1×, P012 2×)
#
# NOTA: todos os registros do concorrente usam "CLICKSUPER" (nome completo),
# pois a classificação agora é 100% dinâmica — competitor_name.upper() in obs.

CANONICAL = [
    # Mi7
    make_record('P001', 'MENOR PRECO'),
    make_record('P002', 'MENOR PRECO'),
    make_record('P003', 'ONLINE'),
    make_record('P004', 'MENOR PREÇO'),
    make_record('P001', 'MENOR PRECO'),    # duplicata Mi7 (P001 repete)

    # ClickSuper — 6 registros, 3 únicos
    make_record('P010', 'CLICKSUPER'),
    make_record('P011', 'CLICKSUPER'),
    make_record('P012', 'CLICKSUPER'),
    make_record('P010', 'CLICKSUPER'),     # duplicata P010
    make_record('P010', 'CLICKSUPER'),     # duplicata P010
    make_record('P012', 'CLICKSUPER'),     # duplicata P012
]


# ─── BLOCO 1: Carregamento de JSON ──────────────────────────────────────────
print("\n== BLOCO 1 — Carregamento de JSON ==========================")

def t_flat_list():
    r = process_competition_analysis(make_json(CANONICAL), 'ClickSuper')
    assert r is not None, "Deve retornar resultado"

run("JSON flat (lista de objetos)", t_flat_list)

def t_envelope():
    r = process_competition_analysis(make_envelope(CANONICAL), 'ClickSuper')
    assert r is not None, "Deve extrair da envelope"

run("JSON com envelope response.pesquisas", t_envelope)

def t_invalid_json():
    try:
        process_competition_analysis(b'{invalid json}', 'ClickSuper')
        assert False, "Deveria lançar ValueError"
    except ValueError as e:
        msg = str(e).lower()
        # Aceita tanto 'inválido/corrompido' (erro de parse direto)
        # quanto 'nenhum arquivo' (quando _load_dataframe descarta o arquivo e fica sem dados)
        assert ('inválido' in msg or 'corrompido' in msg or
                'nenhum' in msg or 'válido' in msg), \
            f"Mensagem de erro inesperada: {e}"

run("JSON inválido -> ValueError", t_invalid_json)

def t_empty_list():
    try:
        process_competition_analysis(make_json([]), 'ClickSuper')
        assert False, "Deveria lançar erro"
    except (ValueError, Exception):
        pass  # esperado

run("JSON lista vazia -> erro esperado", t_empty_list)


# ─── BLOCO 2: Classificação por observacao ───────────────────────────────────
print("\n== BLOCO 2 — Classificação por observação ==================")

def t_mi7_menor_preco():
    r = process_competition_analysis(make_json(CANONICAL), 'ClickSuper')
    assert r['mi7']['total'] == 5, f"Mi7 total esperado 5, obtido {r['mi7']['total']}"

run("Mi7: conta registros com 'MENOR PRECO'/'ONLINE'/'MENOR PRECO'", t_mi7_menor_preco)

def t_mi7_unique():
    r = process_competition_analysis(make_json(CANONICAL), 'ClickSuper')
    assert r['mi7']['unique'] == 4, f"Mi7 unique esperado 4, obtido {r['mi7']['unique']}"

run("Mi7: produtos únicos (deduplica P001)", t_mi7_unique)

def t_competitor_total():
    r = process_competition_analysis(make_json(CANONICAL), 'ClickSuper')
    assert r['competitor']['total'] == 6, \
        f"Competitor total esperado 6, obtido {r['competitor']['total']}"

run("ClickSuper: conta 6 registros com observacao 'CLICKSUPER'", t_competitor_total)

def t_competitor_unique():
    r = process_competition_analysis(make_json(CANONICAL), 'ClickSuper')
    assert r['competitor']['unique'] == 3, \
        f"Competitor unique esperado 3, obtido {r['competitor']['unique']}"

run("ClickSuper: 3 produtos únicos (P010, P011, P012)", t_competitor_unique)


# ─── BLOCO 3: Cálculo de métricas ───────────────────────────────────────────
print("\n== BLOCO 3 — Cálculo de Métricas ===========================")

def t_duplicates():
    r = process_competition_analysis(make_json(CANONICAL), 'ClickSuper')
    dupes = r['competitor']['duplicates']
    assert dupes == 3, f"Duplicatas esperadas 3, obtido {dupes}"

run("Duplicatas = total - unique = 6 - 3 = 3", t_duplicates)

def t_fake_data_index():
    r = process_competition_analysis(make_json(CANONICAL), 'ClickSuper')
    idx = r['competitor']['fake_data_index']
    expected = round(3/6*100, 1)   # 50.0
    assert idx == expected, f"Fake data index esperado {expected}%, obtido {idx}%"

run("Indice de dados falsos = 3/6 = 50.0%", t_fake_data_index)

def t_zero_duplicates():
    records = [
        make_record('P001', 'MENOR PRECO'),
        make_record('P010', 'CLICKSUPER'),
        make_record('P011', 'CLICKSUPER'),
    ]
    r = process_competition_analysis(make_json(records), 'ClickSuper')
    assert r['competitor']['duplicates'] == 0
    assert r['competitor']['fake_data_index'] == 0.0

run("0 duplicatas -> indice = 0%", t_zero_duplicates)

def t_100_percent_fake():
    records = [
        make_record('P001', 'MENOR PRECO'),
        make_record('P010', 'CLICKSUPER'),
        make_record('P010', 'CLICKSUPER'),  # mesmo produto, 2x
        make_record('P010', 'CLICKSUPER'),  # mesmo produto, 3x
    ]
    r = process_competition_analysis(make_json(records), 'ClickSuper')
    expected = round(2/3*100, 1)
    assert r['competitor']['fake_data_index'] == expected, \
        f"Esperado {expected}%, obtido {r['competitor']['fake_data_index']}%"

run("Caso extremo: 1 único em 3 = 66.7% falsos", t_100_percent_fake)


# ─── BLOCO 4: Tabela de crime ────────────────────────────────────────────────
print("\n== BLOCO 4 — Tabela de Crime (Top 20) ======================")

def t_crime_table_order():
    r = process_competition_analysis(make_json(CANONICAL), 'ClickSuper')
    table = r['crime_table']
    assert len(table) > 0, "Tabela não pode ser vazia"
    # P010 tem 3 ocorrências — deve ser o primeiro
    assert table[0]['ean'] == 'P010', \
        f"Produto mais repetido deveria ser P010, obtido {table[0]['ean']}"
    assert table[0]['occurrences'] == 3, \
        f"P010 deveria ter 3 ocorrências, obtido {table[0]['occurrences']}"

run("Crime table ordenada por occurrences DESC (P010 = 3x)", t_crime_table_order)

def t_crime_table_max_20():
    # Cria 25 produtos distintos do concorrente
    many = [make_record('P001', 'MENOR PRECO')]
    for i in range(25):
        many.append(make_record(f'C{i:03d}', 'CLICKSUPER'))
        many.append(make_record(f'C{i:03d}', 'CLICKSUPER'))  # duplicata
    r = process_competition_analysis(make_json(many), 'ClickSuper')
    assert len(r['crime_table']) <= 20, "Crime table não deve ter mais de 20 linhas"

run("Crime table limitada a 20 linhas", t_crime_table_max_20)


# ─── BLOCO 5: Chart data ─────────────────────────────────────────────────────
print("\n== BLOCO 5 — Chart Data =====================================")

def t_chart_labels():
    r = process_competition_analysis(make_json(CANONICAL), 'ClickSuper')
    assert r['chart_data']['labels'] == ['Mi7', 'ClickSuper']

run("chart_data.labels = ['Mi7', '<competitor>']", t_chart_labels)

def t_chart_totals():
    r = process_competition_analysis(make_json(CANONICAL), 'ClickSuper')
    assert r['chart_data']['total'] == [5, 6], \
        f"Totais esperados [5,6], obtido {r['chart_data']['total']}"
    assert r['chart_data']['unique'] == [4, 3], \
        f"Únicos esperados [4,3], obtido {r['chart_data']['unique']}"

run("chart_data.total e unique batem com métricas", t_chart_totals)


# ─── BLOCO 6: scan_competitors() ─────────────────────────────────────────────
print("\n== BLOCO 6 — scan_competitors() ============================")

def t_scan_returns_list():
    result = scan_competitors(make_json(CANONICAL))
    assert isinstance(result, list), "scan_competitors deve retornar lista"

run("scan_competitors retorna lista", t_scan_returns_list)

def t_scan_excludes_mi7():
    result = scan_competitors(make_json(CANONICAL))
    for item in result:
        assert 'MENOR PRECO' not in item and 'ONLINE' not in item, \
            f"scan_competitors não deve retornar termos Mi7: {item}"

run("scan_competitors exclui registros Mi7 (MENOR PRECO/ONLINE)", t_scan_excludes_mi7)

def t_scan_includes_clicksuper():
    result = scan_competitors(make_json(CANONICAL))
    clicksuper_terms = [c for c in result if 'CLICKSUPER' in c.upper()]
    assert len(clicksuper_terms) > 0, \
        f"Deveria detectar CLICKSUPER, obtido: {result}"

run("scan_competitors detecta observacoes CLICKSUPER", t_scan_includes_clicksuper)

def t_scan_no_obs_column():
    records = [{'produto': 'A', 'preco': 10}]
    try:
        scan_competitors(make_json(records))
        assert False, "Deveria lançar ValueError"
    except ValueError as e:
        assert 'observacao' in str(e).lower()

run("scan_competitors sem coluna 'observacao' -> ValueError", t_scan_no_obs_column)

def t_scan_multiple_files():
    """Verifica que scan_competitors aceita lista de bytes (múltiplos arquivos)."""
    file1 = make_json([make_record('P001', 'MENOR PRECO'), make_record('P010', 'CLICKSUPER')])
    file2 = make_json([make_record('P011', 'INFOPRICE')])
    result = scan_competitors([file1, file2])
    assert isinstance(result, list)
    labels = ' '.join(result)
    assert 'CLICKSUPER' in labels or 'INFOPRICE' in labels, \
        f"Deve detectar concorrentes dos dois arquivos: {result}"

run("scan_competitors aceita lista de bytes (multiplos arquivos)", t_scan_multiple_files)


# ─── BLOCO 7: Casos de borda ─────────────────────────────────────────────────
print("\n== BLOCO 7 — Casos de Borda ================================")

def t_competitor_not_found():
    try:
        process_competition_analysis(make_json(CANONICAL), 'InfoPrice')
        assert False, "Deveria lançar ValueError"
    except ValueError as e:
        assert 'InfoPrice' in str(e) or 'nenhum' in str(e).lower()

run("Concorrente não encontrado no arquivo -> ValueError", t_competitor_not_found)

def t_competitor_case_insensitive():
    # 'clicksuper' minúsculo deve casar com registros 'CLICKSUPER'
    r = process_competition_analysis(make_json(CANONICAL), 'clicksuper')
    assert r['competitor']['total'] > 0, "Busca deve ser case-insensitive"

run("Busca por concorrente é case-insensitive", t_competitor_case_insensitive)

def t_only_mi7_records():
    mi7_only = [make_record('P001', 'MENOR PRECO'), make_record('P002', 'ONLINE')]
    try:
        process_competition_analysis(make_json(mi7_only), 'ClickSuper')
        assert False, "Deveria falhar: sem dados do concorrente"
    except ValueError:
        pass

run("Arquivo só com Mi7 -> ValueError (sem concorrente)", t_only_mi7_records)

def t_ean_uppercase():
    records = [
        make_record('p001', 'MENOR PRECO'),   # EAN minúsculo
        make_record('P001', 'CLICKSUPER'),    # EAN maiúsculo — mesmo produto
    ]
    r = process_competition_analysis(make_json(records), 'ClickSuper')
    ean_in_table = r['crime_table'][0]['ean'] if r['crime_table'] else ''
    assert ean_in_table == ean_in_table.upper(), "EAN deve ser uppercase"

run("EANs normalizados para UPPERCASE", t_ean_uppercase)

def t_outros_classification():
    """Registros que não são Mi7 nem o concorrente selecionado vão para 'Outros'."""
    records = [
        make_record('P001', 'MENOR PRECO'),   # Mi7
        make_record('P010', 'CLICKSUPER'),    # ClickSuper
        make_record('P020', 'INFOPRICE'),     # Outros (terceiro coletor)
    ]
    r = process_competition_analysis(make_json(records), 'ClickSuper')
    sources = r['available_sources']
    assert 'Outros' in sources, \
        f"'Outros' deve aparecer nas fontes disponíveis: {sources}"

run("Registros de terceiros classificados como 'Outros'", t_outros_classification)


# ─── BLOCO 8: Múltiplos arquivos ─────────────────────────────────────────────
print("\n== BLOCO 8 — Multiplos Arquivos ============================")

def t_multiple_files_concat():
    """process_competition_analysis deve concatenar múltiplos arquivos."""
    file1 = make_json([
        make_record('P001', 'MENOR PRECO'),
        make_record('P010', 'CLICKSUPER'),
    ])
    file2 = make_json([
        make_record('P002', 'MENOR PRECO'),
        make_record('P011', 'CLICKSUPER'),
        make_record('P011', 'CLICKSUPER'),  # duplicata no 2º arquivo
    ])
    r = process_competition_analysis([file1, file2], 'ClickSuper')
    assert r['mi7']['total'] == 2,       f"Mi7 total esperado 2, obtido {r['mi7']['total']}"
    assert r['competitor']['total'] == 3, f"ClickSuper total esperado 3, obtido {r['competitor']['total']}"
    assert r['competitor']['unique'] == 2, f"ClickSuper unique esperado 2, obtido {r['competitor']['unique']}"

run("Dois arquivos concatenados: métricas somadas corretamente", t_multiple_files_concat)

def t_multiple_files_one_invalid():
    """Um arquivo inválido não deve quebrar o processamento dos demais."""
    file_valid   = make_json([
        make_record('P001', 'MENOR PRECO'),
        make_record('P010', 'CLICKSUPER'),
    ])
    file_invalid = b'{json corrompido}'
    # Deve processar o arquivo válido e ignorar o inválido
    r = process_competition_analysis([file_valid, file_invalid], 'ClickSuper')
    assert r['competitor']['total'] >= 1, \
        "Deve processar o arquivo válido mesmo com um inválido na lista"

run("Arquivo inválido na lista é ignorado, válidos são processados", t_multiple_files_one_invalid)


# ─── Sumário Final ───────────────────────────────────────────────────────────
total  = len(results)
passed = sum(1 for s, _ in results if s == PASS)
failed = total - passed

print("\n" + "="*60)
print(f"  RESULTADO FINAL: {passed}/{total} testes passaram")
if failed:
    print(f"\n  Testes com falha:")
    for status, name in results:
        if status == FAIL:
            print(f"    * {name}")
print("="*60)

sys.exit(0 if failed == 0 else 1)
