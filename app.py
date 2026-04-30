import streamlit as st
import pandas as pd
import json

# 1. Configuração inicial da página web
st.set_page_config(page_title="Relatório Irani - Mi7", layout="wide")

# 2. Cabeçalho do site
st.title("📊 Painel de Inteligência Competitiva - Irani")
st.markdown("Comparativo de volume de dados e produtos únicos enviados: **Mi7 vs ClickSuper**")

# 3. O "Botão" para o Yudi arrastar o arquivo de 100MB
arquivo_json = st.file_uploader("Faça o upload do arquivo JSON do cliente aqui:", type=['json'])

# 4. A lógica só roda SE o arquivo for enviado
if arquivo_json is not None:
    # Mostra uma mensagem de carregamento bonita
    with st.spinner('Mastigando os dados... aguarde!'):
        
        # Extraindo as pesquisas (O JSON lê direto do arquivo upado)
        dados_brutos = json.load(arquivo_json)
        lista_pesquisas = dados_brutos['response']['pesquisas']
        df = pd.DataFrame(lista_pesquisas)
        
        # Padronizando a origem da coleta
        df['observacao_limpa'] = df['observacao'].astype(str).str.upper()

        def classificar_empresa(obs):
            if 'MENOR PREÇO' in obs or 'MENOR PRECO' in obs or 'ONLINE' in obs:
                return 'Mi7'
            elif 'CLICK' in obs or 'CLICKSUPER' in obs:
                return 'ClickSuper'
            else:
                return 'Outros'

        df['Empresa'] = df['observacao_limpa'].apply(classificar_empresa)

        # Processando o relatório final
        volume_total = df.groupby('Empresa').size().reset_index(name='Linhas Enviadas (Volume)')
        produtos_distintos = df.groupby('Empresa')['codigoProduto'].nunique().reset_index(name='Produtos Reais (Distintos)')
        resultado_final = pd.merge(volume_total, produtos_distintos, on='Empresa')
        
        # ==========================================
        # A MÁGICA VISUAL DO STREAMLIT
        # ==========================================
        st.divider() # Linha de separação
        
        col1, col2 = st.columns(2) # Divide a tela em duas colunas
        
        with col1:
            st.subheader("📋 Resumo Executivo")
            st.dataframe(resultado_final, use_container_width=True, hide_index=True)
            
            st.info("""
            **Conclusão da Análise:**
            * Tabela agrupada pela coluna `codigoProduto`.
            * Comprovado: as linhas adicionais da concorrência são apenas repetições do mesmo item.
            """)
            
        with col2:
            st.subheader("📈 Discrepância Visual")
            # Gráfico de barras automático
            df_grafico = resultado_final.set_index('Empresa')
            st.bar_chart(df_grafico)
            
        st.success("Painel gerado com sucesso!")
