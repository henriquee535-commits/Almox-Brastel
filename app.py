import streamlit as st
import pandas as pd
import os

st.set_page_config(page_title="Almoxarifado", layout="wide")

# --- CONFIGURAÇÕES ---
ARQUIVO_ESTOQUE = 'estoque.csv'
ARQUIVO_PLANILHA = 'Almoxarifado.xlsm' # Nome exato e extensão .xlsm corrigidos
SENHA_ACESSO = "Almoxarifado" 

# --- CARREGAR DADOS ---
@st.cache_data
def carregar_ccs():
    try:
        # engine='openpyxl' garante a leitura de arquivos .xlsm
        df_bd = pd.read_excel(ARQUIVO_PLANILHA, sheet_name='BD', engine='openpyxl')
        return df_bd['Centro de Custo'].dropna().unique().tolist()
    except Exception as e:
        return [f"Erro ao ler planilha: {e}"]

lista_cc = carregar_ccs()

if not os.path.exists(ARQUIVO_ESTOQUE):
    pd.DataFrame(columns=['Codigo', 'Descricao', 'Quantidade', 'CC']).to_csv(ARQUIVO_ESTOQUE, index=False)
df = pd.read_csv(ARQUIVO_ESTOQUE)

# --- MENU LATERAL E LOGO ---
try:
    st.sidebar.image("logo.png", use_container_width=True) 
except:
    st.sidebar.warning("Logo não encontrada.")

menu = st.sidebar.radio("Navegação:", ["Consulta (Público)", "Almoxarifado (Restrito)"])

# ==========================================
# TELA 1: CONSULTA DE ESTOQUE (PÚBLICA)
# ==========================================
if menu == "Consulta (Público)":
    st.title("📊 Painel de Estoque")
    
    col1, col2 = st.columns(2)
    col1.metric("Total de Peças", df['Quantidade'].sum() if not df.empty else 0)
    col2.metric("Variedade de Itens", df['Codigo'].nunique() if not df.empty else 0)
    
    st.divider()
    
    busca = st.text_input("🔍 Buscar por Código ou Descrição:")
    if busca:
        df_exibir = df[df['Codigo'].astype(str).str.contains(busca, case=False) | 
                       df['Descricao'].astype(str).str.contains(busca, case=False)]
    else:
        df_exibir = df
        
    st.dataframe(df_exibir, use_container_width=True, hide_index=True)
    
    if not df.empty:
        st.subheader("Quantidade por Centro de Custo")
        st.bar_chart(df.groupby('CC')['Quantidade'].sum())

# ==========================================
# TELA 2: ALMOXARIFADO (RESTRITA)
# ==========================================
elif menu == "Almoxarifado (Restrito)":
    st.title("🔒 Gestão do Almoxarifado")
    
    senha = st.text_input("Senha de acesso:", type="password")
    
    if senha != SENHA_ACESSO:
        if senha != "":
            st.error("Senha incorreta.")
    else:
        st.success("Acesso liberado.")
        
        codigo = st.text_input("Código do Item:")
        descricao = st.text_input("Descrição (obrigatório apenas para novos itens):")
        cc = st.selectbox("Centro de Custo (CC):", lista_cc)
        operacao = st.radio("Operação:", ["1 - Entrada (+)", "2 - Saída (-)"])
        qtd = st.number_input("Quantidade:", min_value=1.0, step=1.0)

        if st.button("Registrar Operação"):
            if not codigo:
                st.warning("Por favor, digite o código do item.")
            else:
                # CORREÇÃO: Forçando 'Codigo' e 'CC' a serem comparados como texto exato
                filtro = (df['Codigo'].astype(str).str.strip() == str(codigo).strip()) & \
                         (df['CC'].astype(str).str.strip() == str(cc).strip())
                
                if filtro.any(): 
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
