import streamlit as st
import pandas as pd
import sqlite3
import os
import uuid
from datetime import datetime, timedelta

# 1. CONFIGURAÇÃO DA PÁGINA (Sempre a primeira linha de comando Streamlit)
st.set_page_config(page_title="Almoxarifado Pro", layout="wide", page_icon="📦")

# --- CONFIGURAÇÕES GERAIS ---
ARQUIVO_PLANILHA = 'Almoxarifado.xlsm' 
SENHA_ACESSO = "Almoxarifado" 
DB_NAME = 'estoque.db'
LIMITE_PESSOAS = 40
TEMPO_INATIVIDADE_MINUTOS = 1 # Faxina rápida de usuários inativos

# --- 2. FUNÇÕES DE BANCO DE DADOS ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS estoque (Codigo TEXT, Descricao TEXT, Quantidade REAL, CC TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS acessos (sessao_id TEXT PRIMARY KEY, ultimo_clique TIMESTAMP)''')
    conn.commit()
    conn.close()

def carregar_estoque():
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT * FROM estoque", conn)
    conn.close()
    return df

@st.cache_data
def carregar_ccs():
    try:
        return pd.read_excel(ARQUIVO_PLANILHA, sheet_name='BD', engine='openpyxl')['Centro de Custo'].dropna().unique().tolist()
    except:
        return ["Setor Padrão"]

# Inicializa o Banco
init_db()

# --- 3. CONTROLE DE ACESSO (CATRACA) ---
if 'sessao_id' not in st.session_state:
    st.session_state.sessao_id = str(uuid.uuid4())

conn = sqlite3.connect(DB_NAME)
c = conn.cursor()
# Limpa inativos
limite_tempo = datetime.now() - timedelta(minutes=TEMPO_INATIVIDADE_MINUTOS)
c.execute("DELETE FROM acessos WHERE ultimo_clique < ?", (limite_tempo,))
# Registra atual
c.execute("INSERT OR REPLACE INTO acessos (sessao_id, ultimo_clique) VALUES (?, ?)", 
          (st.session_state.sessao_id, datetime.now()))
conn.commit()
# Conta ativos
c.execute("SELECT COUNT(*) FROM acessos")
total_ativos = c.fetchone()[0]
conn.close()

# Bloqueio por lotação
if total_ativos > LIMITE_PESSOAS:
    st.warning(f"⚠️ Sistema Lotado ({total_ativos}/{LIMITE_PESSOAS}).")
    if st.button("🔄 Tentar Novamente"): st.rerun()
    st.stop()

# --- 4. CARREGAMENTO DE DADOS ---
lista_cc = carregar_ccs()
df = carregar_estoque()
# Tratamento de erro para códigos vindo do banco
if not df.empty:
    df['Codigo'] = df['Codigo'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()

# --- 5. MENU LATERAL (Define a variável 'menu' antes dos IFs) ---
st.sidebar.title("Menu Principal")
menu = st.sidebar.radio("Navegação", ["📊 Consulta (Público)", "🔒 Almoxarifado (Restrito)"])

# ==========================================
# TELA 1: CONSULTA (PÚBLICA)
# ==========================================
if menu == "📊 Consulta (Público)":
    # Cabeçalho Triplo
    col_l1, col_tit, col_l2 = st.columns([1, 5, 1])
    with col_l1:
        try: st.image("logo1.png", use_container_width=True)
        except: st.caption("Logo 1")
    with col_tit:
        st.title("Painel de Estoque Dinâmico")
    with col_l2:
        try: st.image("logo2.png", use_container_width=True)
        except: st.caption("Logo 2")
    
    st.divider()
    
    # Métricas
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("📦 Peças em Estoque", f"{df['Quantidade'].sum():.0f}" if not df.empty else 0)
    m2.metric("🏷️ Itens Cadastrados", df['Codigo'].nunique() if not df.empty else 0)
    m3.metric("🏢 Setores Ativos", df['CC'].nunique() if not df.empty else 0)
    m4.metric("👁️ Online agora", f"{total_ativos}/{LIMITE_PESSOAS}")
    
    st.divider()
    
    # Filtros
    f_col1, f_col2 = st.columns([3, 1])
    busca = f_col1.text_input("🔍 Pesquisar por código ou descrição do item:")
    setor_f = f_col2.selectbox("🏢 Filtrar Setor:", ["Todos"] + lista_cc)

    df_filt = df.copy()
    if busca:
        df_filt = df_filt[df_filt['Codigo'].str.contains(busca, case=False) | df_filt['Descricao'].str.contains(busca, case=False)]
    if setor_f != "Todos":
        df_filt = df_filt[df_filt['CC'] == setor_f]

    # Abas
    aba_tab, aba_graf = st.tabs(["📋 Lista de Materiais", "📈 Análise Visual"])
    with aba_tab:
        st.dataframe(df_filt, use_container_width=True, hide_index=True, 
                     column_config={"Quantidade": st.column_config.NumberColumn(format="%.0f")})
    with aba_graf:
        if not df_filt.empty:
            st.bar_chart(df_filt.groupby('CC')['Quantidade'].sum())
        else: st.info("Sem dados para o gráfico.")

# ==========================================
# TELA 2: ALMOXARIFADO (RESTRITA)
# ==========================================
elif menu == "🔒 Almoxarifado (Restrito)":
    st.title("Gestão de Movimentação")
    acesso = st.text_input("🔑 Senha do Almoxarifado:", type="password")
    
    if acesso == SENHA_ACESSO:
        # 1. Registro Manual
        with st.expander("📝 Nova Entrada ou Saída", expanded=True):
            with st.form("mov_manual", clear_on_submit=True):
                c1, c2 = st.columns(2)
                cod_m = c1.text_input("Código:")
                desc_m = c2.text_input("Descrição (se novo):")
                c3, c4, c5 = st.columns([2, 2, 1])
                cc_m = c3.selectbox("Setor:", lista_cc)
                op_m = c4.selectbox("Operação:", ["Entrada (+)", "Saída (-)"])
                qtd_m = c5.number_input("Qtd:", min_value=1.0, step=1.0)
                
                if st.form_submit_button("Confirmar", type="primary"):
                    conn = sqlite3.connect(DB_NAME)
                    c = conn.cursor()
                    cod_l, cc_l = cod_m.strip(), cc_m.strip()
                    
                    c.execute("SELECT Descricao FROM estoque WHERE Codigo=? LIMIT 1", (cod_l,))
                    desc_existente = c.fetchone()
                    c.execute("SELECT Quantidade FROM estoque WHERE Codigo=? AND CC=?", (cod_l, cc_l))
                    res = c.fetchone()
                    
                    if res: # Já existe no setor
                        novo = (res[0] + qtd_m) if op_m == "Entrada (+)" else (res[0] - qtd_m)
                        if novo < 0: st.error("Saldo insuficiente!")
                        else:
                            c.execute("UPDATE estoque SET Quantidade=? WHERE Codigo=? AND CC=?", (novo, cod_l, cc_l))
                            st.success("Atualizado!")
                    else: # Novo no setor
                        if op_m == "Saída (-)": st.error("Sem saldo inicial.")
                        else:
                            final_desc = desc_existente[0] if desc_existente else desc_m
                            if not final_desc: st.error("Preencha a descrição.")
                            else:
                                c.execute("INSERT INTO estoque VALUES (?,?,?,?)", (cod_l, final_desc, qtd_m, cc_l))
                                st.success("Cadastrado!")
                    conn.commit(); conn.close(); st.rerun()

        # 2. Importação
        with st.expander("📥 Importar Planilha (Massa)"):
            up = st.file_uploader("Arquivo Excel/CSV (Colunas: Codigo, Descricao, Quantidade, CC)", type=["xlsx", "csv"])
            if up and st.button("Processar Planilha"):
                imp = pd.read_csv(up) if up.name.endswith('.csv') else pd.read_excel(up)
                conn = sqlite3.connect(DB_NAME); c = conn.cursor()
                for _, r in imp.iterrows():
                    c.execute("INSERT INTO estoque VALUES (?,?,?,?)", (str(r['Codigo']), str(r['Descricao']), float(r['Quantidade']), str(r['CC'])))
                conn.commit(); conn.close(); st.success("Importado!"); st.rerun()

        # 3. Exclusão
        with st.expander("🗑️ Corrigir/Excluir Registro"):
            with st.form("del_form"):
                cod_d = st.text_input("Código para APAGAR:")
                cc_d = st.selectbox("Do Setor:", lista_cc, key="del_cc")
                if st.form_submit_button("Remover Permanentemente"):
                    conn = sqlite3.connect(DB_NAME); c = conn.cursor()
                    c.execute("DELETE FROM estoque WHERE Codigo=? AND CC=?", (cod_d.strip(), cc_d.strip()))
                    conn.commit(); conn.close(); st.success("Removido!"); st.rerun()
    elif acesso != "": st.error("Senha inválida.")
