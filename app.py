import streamlit as st
import pandas as pd
import os

# --- CONFIGURAÇÕES DOS ARQUIVOS ---
ARQUIVO_ESTOQUE = 'estoque.csv' # Arquivo que salvará as movimentações
ARQUIVO_PLANILHA = 'Almoxarifado.xlsm' # Substitua pelo nome exato do seu arquivo Excel

# --- 1. CARREGAR CENTROS DE CUSTO ---
@st.cache_data # Memoriza os dados para não ler o Excel a cada clique
def carregar_ccs():
    try:
        df_bd = pd.read_excel(ARQUIVO_PLANILHA, sheet_name='BD')
        # Substitua 'Centro de Custo' pelo nome exato do cabeçalho na sua aba BD
        return df_bd['Centro de Custo'].dropna().unique().tolist()
    except FileNotFoundError:
        return ["Erro: Arquivo Excel não encontrado"]
    except Exception as e:
        return [f"Erro: Verifique o nome da aba e da coluna. Detalhe: {e}"]

lista_cc = carregar_ccs()

# --- 2. CARREGAR ESTOQUE (CSV) ---
if not os.path.exists(ARQUIVO_ESTOQUE):
    df = pd.DataFrame(columns=['Codigo', 'Descricao', 'Quantidade', 'CC'])
    df.to_csv(ARQUIVO_ESTOQUE, index=False)

df = pd.read_csv(ARQUIVO_ESTOQUE)

# --- 3. INTERFACE DO SITE ---
st.title("📦 Almoxarifado Online")

codigo = st.text_input("Código do Item:")
descricao = st.text_input("Descrição (obrigatório apenas para novos itens):")
cc = st.selectbox("Centro de Custo (CC):", lista_cc)
operacao = st.radio("Operação:", ["1 - Entrada (+)", "2 - Saída (-)"])
qtd = st.number_input("Quantidade:", min_value=1.0, step=1.0)

# --- 4. LÓGICA DE PROCESSAMENTO ---
if st.button("Registrar Operação"):
    if not codigo:
        st.warning("Por favor, digite o código do item.")
    elif "Erro" in cc:
        st.error("Resolva o erro da planilha BD antes de registrar.")
    else:
        filtro = (df['Codigo'] == codigo) & (df['CC'] == cc)
        
        if filtro.any(): 
            # Item existe neste CC
            idx = df[filtro].index[0]
            saldo_atual = df.at[idx, 'Quantidade']
            
            if operacao == "1 - Entrada (+)":
                df.at[idx, 'Quantidade'] = saldo_atual + qtd
                st.success(f"Entrada registrada! Novo saldo: {saldo_atual + qtd}")
            else:
                if saldo_atual < qtd:
                    st.error(f"Saldo insuficiente! Disponível: {saldo_atual}")
                else:
                    df.at[idx, 'Quantidade'] = saldo_atual - qtd
                    st.success(f"Saída registrada! Novo saldo: {saldo_atual - qtd}")
            
            df.to_csv(ARQUIVO_ESTOQUE, index=False)
            
        else: 
            # Item NÃO existe neste CC
            if operacao == "2 - Saída (-)":
                st.error("Erro: Não há saldo deste item neste setor para realizar saída.")
            else:
                if not descricao:
                    st.error("Item inédito! Preencha a 'Descrição' para cadastrar.")
                else:
                    novo_item = pd.DataFrame([{'Codigo': codigo, 'Descricao': descricao, 'Quantidade': qtd, 'CC': cc}])
                    df = pd.concat([df, novo_item], ignore_index=True)
                    df.to_csv(ARQUIVO_ESTOQUE, index=False)
                    st.success(f"Novo registro criado e entrada realizada para {cc}")

# --- 5. TABELA DE VISUALIZAÇÃO ---
st.divider()
st.subheader("Visualizar Estoque Atual")
st.dataframe(df, use_container_width=True)