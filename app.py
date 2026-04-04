import streamlit as st
import pandas as pd
import os

# Configuração da página (deixa a tela mais larga e limpa)
st.set_page_config(page_title="Almoxarifado", layout="wide", page_icon="📦")

# --- CONFIGURAÇÕES ---
ARQUIVO_ESTOQUE = 'estoque.csv'
ARQUIVO_PLANILHA = 'Almoxarifado.xlsm' 
SENHA_ACESSO = "Almoxarifado" 

# --- CARREGAR DADOS ---
@st.cache_data
def carregar_ccs():
    try:
        df_bd = pd.read_excel(ARQUIVO_PLANILHA, sheet_name='BD', engine='openpyxl')
        return df_bd['Centro de Custo'].dropna().unique().tolist()
    except Exception as e:
        return [f"Erro ao ler planilha: {e}"]

lista_cc = carregar_ccs()

# Cria o CSV se não existir
if not os.path.exists(ARQUIVO_ESTOQUE):
    pd.DataFrame(columns=['Codigo', 'Descricao', 'Quantidade', 'CC']).to_csv(ARQUIVO_ESTOQUE, index=False)

# SOLUÇÃO DO ITEM INÉDITO: Força a leitura do Código sempre como Texto puro e remove casas decimais
df = pd.read_csv(ARQUIVO_ESTOQUE, dtype={'Codigo': str})
df['Codigo'] = df['Codigo'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()

# --- MENU LATERAL ---
menu = st.sidebar.radio("Navegação:", ["Consulta (Público)", "Almoxarifado (Restrito)"])

# ==========================================
# TELA 1: CONSULTA DE ESTOQUE (PÚBLICA)
# ==========================================
if menu == "Consulta (Público)":
    
    # Cabeçalho com Logo
    col_logo, col_titulo = st.columns([1, 4])
    with col_logo:
        try:
            st.image("logo.png", width=150)
        except:
            st.info("Sua logo aparecerá aqui")
            
    with col_titulo:
        st.title("Painel de Estoque")
        st.markdown("*Acompanhe a disponibilidade de materiais em tempo real.*")
    
    st.divider()
    
    # Indicadores visuais
    col1, col2, col3 = st.columns(3)
    col1.metric("📦 Total de Peças", df['Quantidade'].sum() if not df.empty else 0)
    col2.metric("🏷️ Variedade de Itens", df['Codigo'].nunique() if not df.empty else 0)
    col3.metric("🏢 Setores Atendidos", df['CC'].nunique() if not df.empty else 0)
    
    st.divider()
    
    # Busca e Tabela
    busca = st.text_input("🔍 O que você procura? (Digite o Código ou Descrição):")
    if busca:
        df_exibir = df[df['Codigo'].str.contains(busca, case=False, na=False) | 
                       df['Descricao'].astype(str).str.contains(busca, case=False, na=False)]
    else:
        df_exibir = df
        
    # Tabela com colunas renomeadas e formatadas
    st.dataframe(
        df_exibir, 
        use_container_width=True, 
        hide_index=True,
        column_config={
            "Codigo": st.column_config.TextColumn("Código do Item"),
            "Descricao": st.column_config.TextColumn("Descrição"),
            "Quantidade": st.column_config.NumberColumn("Qtd. Disponível"),
            "CC": st.column_config.TextColumn("Centro de Custo")
        }
    )

# ==========================================
# TELA 2: ALMOXARIFADO (RESTRITA)
# ==========================================
elif menu == "Almoxarifado (Restrito)":
    
    # Coloca a logo pequena na barra lateral para a equipe do almoxarifado
    try:
        st.sidebar.image("logo.png", use_container_width=True) 
    except:
        pass

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
                codigo_limpo = str(codigo).strip()
                cc_limpo = str(cc).strip()
                
                filtro = (df['Codigo'] == codigo_limpo) & (df['CC'].astype(str).str.strip() == cc_limpo)
                
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
                            novo_item = pd.DataFrame([{'Codigo': codigo_limpo, 'Descricao': descricao, 'Quantidade': qtd, 'CC': cc_limpo}])
                            df = pd.concat([df, novo_item], ignore_index=True)
                            df.to_csv(ARQUIVO_ESTOQUE, index=False)
                            st.success(f"Novo registro criado e entrada realizada para {cc}")
