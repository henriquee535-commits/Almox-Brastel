import streamlit as st
import pandas as pd
import sqlite3
import os

st.set_page_config(page_title="Almoxarifado", layout="wide", page_icon="📦")

# --- CONFIGURAÇÕES ---
ARQUIVO_PLANILHA = 'Almoxarifado.xlsm' 
SENHA_ACESSO = "Almoxarifado" 
DB_NAME = 'estoque.db'

# --- 1. INICIAR BANCO DE DADOS ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS estoque (
            Codigo TEXT,
            Descricao TEXT,
            Quantidade REAL,
            CC TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def carregar_estoque():
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT * FROM estoque", conn)
    conn.close()
    return df

# --- 2. CARREGAR CENTROS DE CUSTO ---
@st.cache_data
def carregar_ccs():
    try:
        df_bd = pd.read_excel(ARQUIVO_PLANILHA, sheet_name='BD', engine='openpyxl')
        return df_bd['Centro de Custo'].dropna().unique().tolist()
    except Exception as e:
        return [f"Erro ao ler planilha: {e}"]

lista_cc = carregar_ccs()
df = carregar_estoque()

# --- MENU LATERAL ---
menu = st.sidebar.radio("Navegação:", ["Consulta (Público)", "Almoxarifado (Restrito)"])

# ==========================================
# TELA 1: CONSULTA DE ESTOQUE (PÚBLICA)
# ==========================================
if menu == "Consulta (Público)":
    
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
    
    col1, col2, col3 = st.columns(3)
    col1.metric("📦 Total de Peças", df['Quantidade'].sum() if not df.empty else 0)
    col2.metric("🏷️ Variedade de Itens", df['Codigo'].nunique() if not df.empty else 0)
    col3.metric("🏢 Setores Atendidos", df['CC'].nunique() if not df.empty else 0)
    
    st.divider()
    
    busca = st.text_input("🔍 O que você procura? (Digite o Código ou Descrição):")
    if busca:
        df_exibir = df[df['Codigo'].str.contains(busca, case=False, na=False) | 
                       df['Descricao'].astype(str).str.contains(busca, case=False, na=False)]
    else:
        df_exibir = df
        
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
        descricao = st.text_input("Descrição (obrigatório apenas para itens inéditos na empresa):")
        cc = st.selectbox("Centro de Custo (CC):", lista_cc)
        operacao = st.radio("Operação:", ["1 - Entrada (+)", "2 - Saída (-)"])
        qtd = st.number_input("Quantidade:", min_value=1.0, step=1.0)

        if st.button("Registrar Operação"):
            if not codigo:
                st.warning("Por favor, digite o código do item.")
            else:
                codigo_limpo = str(codigo).strip()
                cc_limpo = str(cc).strip()
                
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                
                # 1. VERIFICA SE O CÓDIGO JÁ EXISTE NA EMPRESA (Qualquer CC)
                c.execute("SELECT Descricao FROM estoque WHERE Codigo=? LIMIT 1", (codigo_limpo,))
                desc_banco = c.fetchone()
                desc_padrao = desc_banco[0] if desc_banco else None
                
                # 2. VERIFICA O SALDO NESTE CC ESPECÍFICO
                c.execute("SELECT Quantidade FROM estoque WHERE Codigo=? AND CC=?", (codigo_limpo, cc_limpo))
                resultado = c.fetchone()
                
                if resultado: 
                    # ITEM JÁ EXISTE NESTE CC
                    saldo_atual = resultado[0]
                    
                    if operacao == "1 - Entrada (+)":
                        novo_saldo = saldo_atual + qtd
                        c.execute("UPDATE estoque SET Quantidade=? WHERE Codigo=? AND CC=?", (novo_saldo, codigo_limpo, cc_limpo))
                        st.success(f"Entrada registrada! Novo saldo: {novo_saldo}")
                    else:
                        if saldo_atual < qtd:
                            st.error(f"Saldo insuficiente! Disponível: {saldo_atual}")
                        else:
                            novo_saldo = saldo_atual - qtd
                            c.execute("UPDATE estoque SET Quantidade=? WHERE Codigo=? AND CC=?", (novo_saldo, codigo_limpo, cc_limpo))
                            st.success(f"Saída registrada! Novo saldo: {novo_saldo}")
                else: 
                    # ITEM NÃO EXISTE NESTE CC AINDA
                    if operacao == "2 - Saída (-)":
                        st.error("Erro: Não há saldo deste item neste setor para realizar saída.")
                    else:
                        # É uma Entrada. Vamos checar a descrição.
                        if desc_padrao:
                            # Se a descrição já existe no banco, ignora a digitada e usa a oficial
                            c.execute("INSERT INTO estoque (Codigo, Descricao, Quantidade, CC) VALUES (?, ?, ?, ?)", 
                                      (codigo_limpo, desc_padrao, qtd, cc_limpo))
                            st.success(f"Nova entrada para o setor {cc_limpo}. Descrição automática utilizada: '{desc_padrao}'.")
                        else:
                            # Se é a primeira vez que a empresa vê esse código, exige a descrição
                            if not descricao:
                                st.error("Item 100% inédito na empresa! Preencha a 'Descrição' para cadastrar.")
                            else:
                                c.execute("INSERT INTO estoque (Codigo, Descricao, Quantidade, CC) VALUES (?, ?, ?, ?)", 
                                          (codigo_limpo, descricao, qtd, cc_limpo))
                                st.success(f"Item inédito cadastrado com sucesso para o setor {cc_limpo}!")
                
                conn.commit()
                conn.close()
                st.rerun()
