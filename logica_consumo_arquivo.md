# Lógica de Consumo do Arquivo JSON — Mi7 Intelligence Dashboard

> **Arquivo de referência:** `app/services/analysis_service.py`
> **Autor técnico:** Sistema Mi7 Intelligence
> **Data:** Maio/2026

---

## Visão Geral

O sistema recebe um arquivo `.json` via upload no navegador, processa tudo **em memória** (sem gravar no disco) e retorna métricas competitivas para o dashboard. A lógica foi construída em Python usando as bibliotecas **Pandas** (análise de dados) e **json** (parsing nativo do Python).

---

## Fluxo Completo — Passo a Passo

### ETAPA 1 — Recebimento e Leitura do Arquivo

**Arquivo:** `app/dashboard/routes.py`

O arquivo JSON é recebido como um upload HTTP via formulário. O conteúdo é lido diretamente para a memória como bytes, **sem salvar em disco**.

```python
json_bytes = uploaded_file.read()
```

Em seguida, os bytes são enviados para o serviço de análise.

---

### ETAPA 2 — Parsing do JSON

**Arquivo:** `app/services/analysis_service.py`

O conteúdo em bytes é decodificado para UTF-8 e convertido para uma estrutura Python (dicionários e listas):

```python
data = json.loads(json_bytes.decode('utf-8'))
```

> [!NOTE]
> A decodificação UTF-8 garante compatibilidade com caracteres especiais do português (acentos, cedilha etc.).

---

### ETAPA 3 — Detecção Inteligente da Estrutura do JSON

Esta é a etapa mais crítica. JSONs de diferentes sistemas têm estruturas diferentes — o dado principal pode estar aninhado dentro de chaves como `"data"`, `"results"`, `"items"` etc.

Foi implementada uma **função recursiva** chamada `find_largest_list_of_dicts` que navega toda a árvore do JSON e encontra o maior conjunto de registros:

```python
def find_largest_list_of_dicts(node):
    best_list = []
    if isinstance(node, list):
        # Se é uma lista de objetos → candidato principal
        if len(node) > 0 and isinstance(node[0], dict):
            best_list = node
        # Busca recursivamente nas sub-listas
        for item in node:
            res = find_largest_list_of_dicts(item)
            if len(res) > len(best_list):
                best_list = res
    elif isinstance(node, dict):
        # Caso especial: dicionário de listas de mesmo tamanho
        # (formato colunar tipo {"campo1": [...], "campo2": [...]})
        if all(isinstance(v, list) for v in node.values()):
            lengths = {len(v) for v in node.values()}
            if len(lengths) == 1:
                return node  # Pandas converte direto para DataFrame
        # Busca recursivamente nos valores do dicionário
        for k, v in node.items():
            res = find_largest_list_of_dicts(v)
            if len(res) > len(best_list):
                best_list = res
    return best_list
```

**Lógica de decisão:**
- Se o nó é uma **lista de dicionários** → é o conjunto de dados (cada dicionário = uma linha)
- Se o nó é um **dicionário de listas com mesmo tamanho** → é um formato colunar (cada chave = uma coluna)
- Sempre escolhe o nó com **mais registros** (maior lista), garantindo que pega os dados reais e não metadados

Após encontrar os dados, eles são convertidos em um **DataFrame Pandas**:

```python
df = pd.DataFrame(extracted)
```

---

### ETAPA 4 — Normalização de Colunas

**Função:** `_detect_columns(df)`

JSONs de diferentes sistemas de coleta de preços usam nomes de colunas variados. A função detecta variações e padroniza os nomes para que o restante do código funcione de forma consistente:

| Categoria | Variações aceitas | Nome padronizado |
|-----------|-------------------|-----------------|
| Identificador do produto | `codigoProduto`, `ean`, `gtin`, `barcode`, `codbarras` | `ean` |
| Origem / coletor | `source`, `coletor`, `origem`, `codigoConcorrente` | `source` |
| Nome do produto | `nome`, `descricao`, `produto`, `product_name` | `product_name` |

A detecção é **case-insensitive** (ignora maiúsculas/minúsculas).

---

### ETAPA 5 — Classificação por Origem (Lógica de Negócio Mi7)

Esta é a regra de negócio central. O sistema determina se cada registro pertence à **Mi7** ou a um **concorrente** analisando o conteúdo da coluna `observacao`:

```python
def classificar_empresa(obs):
    if 'MENOR PREÇO' in obs or 'MENOR PRECO' in obs or 'ONLINE' in obs:
        return 'Mi7'
    elif competitor_name.upper() in obs:
        return competitor_name
    elif 'CLICK' in obs or 'CLICKSUPER' in obs:
        return 'ClickSuper'
    else:
        return 'Outros'
```

**Regras de classificação:**
- ✅ **Mi7:** Observação contém `"MENOR PREÇO"`, `"MENOR PRECO"` ou `"ONLINE"`
- ✅ **Concorrente selecionado:** Observação contém o nome do concorrente (ex: `"ClickSuper"`)
- ✅ **Outros:** Qualquer outra observação

---

### ETAPA 6 — Limpeza dos Dados

Antes de calcular métricas, os dados são higienizados:

```python
df['ean'] = df['ean'].astype(str).str.strip().str.upper()
df['source'] = df['source'].astype(str).str.strip()

# Remove registros sem EAN
df = df[df['ean'].notna() & (df['ean'] != '') & (df['ean'] != 'NAN')]
```

- Remove espaços em branco nos extremos
- Padroniza EANs em maiúsculas
- Remove linhas com EAN nulo ou vazio

---

### ETAPA 7 — Cálculo das Métricas

#### Separação dos DataFrames

```python
df_mi7        = df[df['source'].str.contains('Mi7', case=False)]
df_competitor = df[df['source'].str.contains(competitor_name, case=False)]
```

#### Métricas calculadas

| Métrica | Cálculo | Significado |
|---------|---------|-------------|
| `mi7_total` | `len(df_mi7)` | Total de registros coletados pela Mi7 |
| `mi7_unique` | `df_mi7['ean'].nunique()` | Produtos únicos da Mi7 |
| `comp_total` | `len(df_competitor)` | Total de registros do concorrente |
| `comp_unique` | `df_competitor['ean'].nunique()` | Produtos únicos do concorrente |
| `comp_duplicates` | `comp_total - comp_unique` | Quantidade de duplicatas do concorrente |
| `fake_data_index` | `(duplicatas / total) × 100` | **Índice de dados falsos (%)** — % de registros repetidos |

#### "Prova do Crime" — Produtos mais repetidos

Identifica os produtos (EANs) que o concorrente mais repetiu, ordenado do maior para o menor número de ocorrências:

```python
crime_table = (
    df_competitor
    .groupby('ean')
    .size()
    .reset_index(name='occurrences')
    .sort_values('occurrences', ascending=False)
    .head(20)  # Top 20
)
```

---

## Scan Automático de Concorrentes

Antes da análise principal, o sistema também oferece uma **detecção automática** dos concorrentes presentes no arquivo:

```python
def scan_competitors(json_bytes: bytes) -> list:
    df = _load_dataframe(json_bytes)
    obs_upper = df['observacao'].astype(str).str.upper().str.strip()

    # Exclui registros Mi7
    mi7_mask = (
        obs_upper.str.contains('MENOR PREÇO') |
        obs_upper.str.contains('MENOR PRECO') |
        obs_upper.str.contains('ONLINE')
    )

    # O que sobrar = concorrentes
    competitors = sorted(obs_upper[~mi7_mask].unique().tolist())
    return competitors[:50]  # Limita a 50
```

Isso permite que o usuário selecione o concorrente diretamente da interface, **sem precisar digitar o nome manualmente**.

---

## Padrão de Segurança (PRG)

O sistema usa o padrão **Post/Redirect/Get** para evitar reenvio acidental do formulário:

```
1. Usuário faz upload (POST)
   ↓
2. Sistema processa e salva na sessão
   ↓
3. Redireciona (302) para GET
   ↓
4. GET lê da sessão, exibe o resultado e limpa a sessão
```

---

## Resumo Executivo

```
Arquivo JSON (upload) 
    → Decodificação UTF-8 
    → Busca recursiva dos dados 
    → DataFrame Pandas 
    → Normalização de colunas 
    → Classificação Mi7 vs Concorrente (via coluna 'observacao') 
    → Limpeza 
    → Cálculo de métricas (total, únicos, duplicatas, índice de dados falsos) 
    → Prova do Crime (top 20 EANs mais repetidos) 
    → Dashboard
```

> [!IMPORTANT]
> Todo o processamento ocorre **100% em memória**. Nenhum arquivo é salvo em disco no servidor. Os resultados são temporariamente armazenados na **sessão do usuário** (server-side session) e descartados após a exibição.
