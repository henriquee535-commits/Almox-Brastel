import streamlit as st
import pandas as pd
import sqlite3
import uuid
from datetime import datetime, timedelta

# 1. CONFIGURAÇÃO DA PÁGINA (Deve ser o primeiro comando Streamlit)
st.set_page_config(page_title="Almoxarifado Pro", layout="wide", page_icon="📦")

# --- CONFIGURAÇÕES GERAIS ---
ARQUIVO_PLANILHA = 'Almoxarifado.xlsm' 
SENHA_ACESSO = "1234" 
DB_NAME = 'estoque.db'
LIMITE_PESSOAS = 40
TEMPO_INATIVIDADE = 1 # Minutos

# --- 2. BANCO DE DADOS ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS estoque (Codigo TEXT, Descricao TEXT, Quantidade REAL, CC TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS acessos (sessao_id TEXT PRIMARY KEY, ultimo_clique TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

def carregar_estoque():
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT * FROM estoque", conn)
    conn.close()
    return df

@st.cache_data
def carregar_ccs():
    try:
        df_bd = pd.read_excel(ARQUIVO_PLANILHA, sheet_name='BD', engine='openpyxl')
        return df_bd['Centro de Custo'].dropna().unique().tolist()
    except:
        return ["Setor Geral"]

# --- 3. CONTROLE DE ACESSO (CATRACA) ---
if 'sessao_id' not in st.session_state:
    st.session_state.sessao_id = str(uuid.uuid4())

conn = sqlite3.connect(DB_NAME)
c = conn.cursor()
tempo_limite = datetime.now() - timedelta(minutes=TEMPO_INATIVIDADE)
c.execute("DELETE FROM acessos WHERE ultimo_clique < ?", (tempo_limite,))
c.execute("INSERT OR REPLACE INTO acessos VALUES (?, ?)", (st.session_state.sessao_id, datetime.now()))
conn.commit()
c.execute("SELECT COUNT(*) FROM acessos")
total_ativos = c.fetchone()[0]
conn.close()

if total_ativos > LIMITE_PESSOAS:
    st.warning(f"⚠️ Sistema Lotado ({total_ativos}/{LIMITE_PESSOAS}). Tente em 1 minuto.")
    st.stop()

# --- 4. NAVEGAÇÃO ---
df = carregar_estoque()
lista_cc = carregar_ccs()

st.sidebar.title("Navegação")
menu = st.sidebar.radio("Ir para:", ["📊 Consulta", "🔒 Almoxarifado"])

# ==========================================
# TELA 1: CONSULTA
# ==========================================
if menu == "📊 Consulta":
    col_l1, col_tit, col_l2 = st.columns([1, 5, 1])
    with col_l1:
        try: st.image("logo1.png", use_container_width=True)
        except: st.write("Logo 1")
    with col_tit: st.title("Painel de Estoque")
    with col_l2:
        try: st.image("logo2.png", use_container_width=True)
        except: st.write("Logo 2")
    
    st.divider()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("📦 Peças", f"{df['Quantidade'].sum():.0f}" if not df.empty else 0)
    m2.metric("🏷️ Itens", df['Codigo'].nunique() if not df.empty else 0)
    m3.metric("🏢 Setores", df['CC'].nunique() if not df.empty else 0)
    m4.metric("👁️ Online", f"{total_ativos}/{LIMITE_PESSOAS}")
    
    st.divider()
    busca = st.text_input("🔍 Pesquisar Código ou Descrição:")
    df_filt = df.copy()
    if busca:
        df_filt = df[df['Codigo'].astype(str).str.contains(busca, case=False) | df['Descricao'].str.contains(busca, case=False)]
    
    st.dataframe(df_filt, use_container_width=True, hide_index=True)

# ==========================================
# TELA 2: ALMOXARIFADO
# ==========================================
else:
    st.title("🔒 Área Restrita")
    senha = st.text_input("Senha:", type="password")
    if senha == SENHA_ACESSO:
        with st.form("registro"):
            c1, c2 = st.columns(2)
            cod = c1.text_input("Código:")
            desc = c2.text_input("Descrição (se novo):")
            c3, c4, c5 = st.columns([2, 2, 1])
            cc_sel = c3.selectbox("Setor:", lista_cc)
            op = c4.selectbox("Operação:", ["Entrada", "Saída"])
            qtd = c5.number_input("Qtd:", min_value=1.0)
            if st.form_submit_button("Confirmar"):
                conn = sqlite3.connect(DB_NAME); c = conn.cursor()
                c.execute("SELECT Quantidade FROM estoque WHERE Codigo=? AND CC=?", (cod, cc_sel))
                res = c.fetchone()
                if res:
                    novo = (res[0] + qtd) if op == "Entrada" else (res[0] - qtd)
                    c.execute("UPDATE estoque SET Quantidade=? WHERE Codigo=? AND CC=?", (max(0, novo), cod, cc_sel))
                else:
                    c.execute("INSERT INTO estoque VALUES (?,?,?,?)", (cod, desc, qtd, cc_sel))
                conn.commit(); conn.close(); st.rerun()
