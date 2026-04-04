import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import sqlite3
import uuid
from datetime import datetime, timedelta
import io
import base64

# 1. CONFIGURAÇÃO DA PÁGINA
st.set_page_config(page_title="Inventário Brastel", layout="wide", page_icon="📦")

# --- CONFIGURAÇÕES ---
ARQUIVO_PLANILHA = 'Almoxarifado.xlsm'
SENHA_ACESSO = "1234"
SENHA_ZERAR_ESTOQUE = "admin123"
DB_NAME = 'estoque.db'
LIMITE_PESSOAS = 40
TEMPO_INATIVIDADE = 1

# --- CSS GLOBAL + RESPONSIVO ---
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Sora', sans-serif;
}

/* Ocultar barra superior do Streamlit para visual mais limpo */
header {visibility: hidden;}

/* Botões e Alertas */
.stButton > button {
    background: linear-gradient(135deg, #1a3a4a, #0d5c8a);
    color: white; border: none; border-radius: 8px;
    font-family: 'Sora', sans-serif; font-weight: 600;
    padding: 0.5rem 1.5rem; transition: opacity 0.2s;
}
.stButton > button:hover { opacity: 0.88; color: white; }
.stAlert { border-radius: 10px; }
</style>
""", unsafe_allow_html=True)

# --- BANCO DE DADOS ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS estoque
                 (Codigo TEXT, Descricao TEXT, Quantidade REAL, CC TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS acessos
                 (sessao_id TEXT PRIMARY KEY, ultimo_clique TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS centros_custo
                 (nome TEXT PRIMARY KEY)''')
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
    conn = sqlite3.connect(DB_NAME)
    df_cc = pd.read_sql_query("SELECT nome FROM centros_custo ORDER BY nome", conn)
    lista_cc = df_cc['nome'].tolist()
    
    if not lista_cc:
        try:
            df_bd = pd.read_excel(ARQUIVO_PLANILHA, sheet_name='BD', engine='openpyxl')
            lista_cc = df_bd['Centro de Custo'].dropna().unique().tolist()
            c = conn.cursor()
            for cc in lista_cc:
                c.execute("INSERT OR IGNORE INTO centros_custo VALUES (?)", (cc,))
            conn.commit()
        except:
            lista_cc = ["Setor Geral"]
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO centros_custo VALUES (?)", ("Setor Geral",))
            conn.commit()
            
    conn.close()
    return lista_cc

def buscar_descricao_por_codigo(cod):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT DISTINCT Descricao FROM estoque WHERE Codigo=?", (cod,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def gerar_template_xlsx():
    template_df = pd.DataFrame({
        'Codigo':     ['ABC001', 'ABC002'],
        'Descricao':  ['Parafuso M8', 'Cabo Elétrico 2,5mm'],
        'Quantidade': [100, 50],
        'CC':         ['Setor Geral', 'Manutenção']
    })
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        template_df.to_excel(writer, index=False, sheet_name='Inventario')
    return buf.getvalue()

def gerar_template_depara():
    df = pd.DataFrame({'De': ['Setor Antigo 1', 'Setor Antigo 2'], 'Para': ['Setor Novo 1', 'Setor Novo 2']})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='DePara')
    return buf.getvalue()

def logo_para_base64(path):
    for tentativa in [path, path.replace('.png', '.jpg'), path.replace('.png', '.jpeg')]:
        try:
            with open(tentativa, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            ext = tentativa.rsplit('.', 1)[-1].lower()
            mime = 'image/png' if ext == 'png' else 'image/jpeg'
            return f"data:{mime};base64,{data}"
        except FileNotFoundError:
            continue
    return None

# --- CONTROLE DE ACESSO ---
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

# --- NAVEGAÇÃO ---
df = carregar_estoque()
lista_cc = carregar_ccs()

st.sidebar.title("Navegação")
menu = st.sidebar.radio("Ir para:", ["📊 Consulta", "🔒 Almoxarifado"])

# ==========================================
# TELA 1: CONSULTA
# ==========================================
if menu == "📊 Consulta":

    src1 = logo_para_base64("logo1.png")
    src2 = logo_para_base64("logo2.png")

    img1 = f'<img class="logo1-img" src="{src1}">' if src1 else '<span style="color:#102a43;font-weight:700;">LOGO 1</span>'
    img2 = f'<img class="logo2-img" src="{src2}">' if src2 else '<span style="color:#102a43;font-weight:700;">LOGO 2</span>'

    df_ativos = df[df['Quantidade'] > 0]

    total_pecas   = f"{df_ativos['Quantidade'].sum():.0f}" if not df_ativos.empty else "0"
    total_itens   = str(df_ativos['Codigo'].nunique())     if not df_ativos.empty else "0"
    total_setores = str(df_ativos['CC'].nunique())         if not df_ativos.empty else "0"

    components.html(f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
      * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Sora', sans-serif; }}

      .header-container {{
        display: flex; align-items: center; justify-content: space-between;
        padding: 18px 32px; border-radius: 16px; margin-bottom: 16px;
        background: linear-gradient(135deg, #f0f4f8 0%, #d9e2ec 100%);
        box-shadow: 0 4px 12px rgba(0,0,0,0.05); border: 1px solid #e2e8f0; gap: 16px;
      }}
      
      /* Travando as larguras laterais para garantir simetria e o título centralizado */
      .header-logo-left {{
        display: flex; align-items: center; justify-content: flex-start;
        flex: 0 0 160px;
      }}
      .header-right {{
        display: flex; flex-direction: column; align-items: flex-end; justify-content: center;
        gap: 8px; flex: 0 0 160px;
      }}

      /* Ajuste individual das logos para equilíbrio visual */
      .logo1-img {{ height: 60px; max-width: 160px; object-fit: contain; mix-blend-mode: darken; }}
      .logo2-img {{ height: 42px; max-width: 140px; object-fit: contain; mix-blend-mode: darken; }}

      .header-title-block {{ text-align: center; flex: 1; padding: 0 16px; }}
      .header-title-block h1 {{ font-size: 1.8rem; font-weight: 700; color: #102a43; letter-spacing: 0.02em; text-transform: uppercase; line-height: 1.15; }}
      .header-title-block p {{ font-size: 0.75rem; color: #334e68; margin-top: 5px; font-weight: 600; letter-spacing: 0.22em; text-transform: uppercase; }}
      
      .header-badge {{ background: rgba(16,42,67,0.1); border: 1px solid rgba(16,42,67,0.2); color: #102a43; border-radius: 20px; padding: 4px 12px; font-size: 0.74rem; font-weight: 600; letter-spacing: 0.06em; white-space: nowrap; }}

      .metrics-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 4px; }}
      .metric-card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }}
      .metric-label {{ font-size: 0.78rem; color: #718096; font-weight: 600; margin-bottom: 4px; }}
      .metric-value {{ font-size: 1.9rem; font-weight: 700; color: #1a202c; line-height: 1.1; }}

      @media (max-width: 640px) {{
        .header-container {{ flex-direction: column; padding: 14px 16px; gap: 10px; text-align: center; }}
        .header-logo-left, .header-right {{ flex: auto; align-items: center; justify-content: center; width: 100%; flex-direction: row; }}
        .header-title-block h1 {{ font-size: 1.2rem; }}
        .metrics-grid {{ grid-template-columns: 1fr; gap: 8px; }}
        .metric-card  {{ padding: 10px 14px; display: flex; justify-content: space-between; align-items: center; }}
        .metric-label {{ margin-bottom: 0; font-size: 0.82rem; }}
        .metric-value {{ font-size: 1.35rem; }}
      }}
    </style>
    </head>
    <body>

    <div class="header-container">
      <div class="header-logo-left">{img1}</div>
      <div class="header-title-block">
        <h1>Inventário Brastel</h1>
        <p>Almoxarifado</p>
      </div>
      <div class="header-right">
        {img2}
        <span class="header-badge">🟢 {total_ativos}/{LIMITE_PESSOAS} online</span>
      </div>
    </div>

    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-label">📦 Total de Peças</div>
        <div class="metric-value">{total_pecas}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">🏷️ Itens Únicos</div>
        <div class="metric-value">{total_itens}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">🏢 Setores</div>
        <div class="metric-value">{total_setores}</div>
      </div>
    </div>

    </body>
    </html>
    """, height=260, scrolling=False)

    st.divider()

    busca = st.text_input("🔍 Pesquisar Código ou Descrição:")
    df_filt = df_ativos.copy() 
    if busca:
        df_filt = df_filt[
            df_filt['Codigo'].astype(str).str.contains(busca, case=False) |
            df_filt['Descricao'].str.contains(busca, case=False, na=False)
        ]

    st.dataframe(df_filt, use_container_width=True, hide_index=True)

# ==========================================
# TELA 2: ALMOXARIFADO
# ==========================================
else:
    st.title("🔒 Área Restrita — Almoxarifado")
    senha = st.text_input("Senha:", type="password")

    if senha == SENHA_ACESSO:

        tab1, tab2, tab3, tab4 = st.tabs([
            "📝 Registro Individual",
            "📤 Carga em Massa",
            "🏢 Gerenciar Setores",
            "⚠️ Limpar Dados"
        ])

        # ------------------------------------------
        # TAB 1: REGISTRO INDIVIDUAL
        # ------------------------------------------
        with tab1:
            with st.form("registro"):
                c1, c2 = st.columns(2)
                cod        = c1.text_input("Código:")
                desc_input = c2.text_input("Descrição (somente para itens novos):")

                c3, c4, c5 = st.columns([2, 2, 1])
                cc_sel = c3.selectbox("Setor (Centro de Custo):", lista_cc)
                op     = c4.selectbox("Operação:", ["Entrada", "Saída"])
                qtd    = c5.number_input("Qtd:", min_value=1.0)

                if st.form_submit_button("✅ Confirmar"):
                    if not cod:
                        st.error("⛔ Informe o Código do item.")
                    else:
                        desc_existente = buscar_descricao_por_codigo(cod)
                        if desc_existente and desc_input and desc_input.strip() != desc_existente.strip():
                            st.error(f"⛔ Conflito de Descrição! O código **{cod}** já está cadastrado com:\n\n**\"{desc_existente}\"**")
                        else:
                            desc_final = desc_existente if desc_existente else desc_input
                            conn = sqlite3.connect(DB_NAME)
                            cur  = conn.cursor()
                            cur.execute("SELECT Quantidade FROM estoque WHERE Codigo=? AND CC=?", (cod, cc_sel))
                            res = cur.fetchone()
                            if res:
                                novo = (res[0] + qtd) if op == "Entrada" else max(0, res[0] - qtd)
                                cur.execute("UPDATE estoque SET Quantidade=? WHERE Codigo=? AND CC=?", (novo, cod, cc_sel))
                                st.success(f"✅ {op} registrada. Saldo atualizado: {novo:.0f}")
                            else:
                                if op == "Saída":
                                    st.warning("⚠️ Item não encontrado neste setor.")
                                else:
                                    cur.execute("INSERT INTO estoque VALUES (?,?,?,?)", (cod, desc_final, qtd, cc_sel))
                                    st.success("✅ Item novo cadastrado.")
                            conn.commit()
                            conn.close()
                            st.cache_data.clear()
                            st.rerun()

        # ------------------------------------------
        # TAB 2: CARGA EM MASSA
        # ------------------------------------------
        with tab2:
            st.info("Upload de arquivo Excel (.xlsx) com colunas: `Codigo` | `Descricao` | `Quantidade` | `CC`")
            st.download_button("⬇️ Template Inventário", gerar_template_xlsx(), "template_inventario.xlsx")
            arquivo = st.file_uploader("Arquivo de Inventário (.xlsx):", type=["xlsx"], key="upload_massa")

            if arquivo:
                try:
                    df_upload = pd.read_excel(arquivo, engine='openpyxl')
                    faltando = {'Codigo', 'Descricao', 'Quantidade', 'CC'} - set(df_upload.columns)
                    if faltando:
                        st.error(f"⛔ Colunas ausentes: {', '.join(faltando)}")
                    else:
                        if st.button("🚀 Processar Importação"):
                            conn = sqlite3.connect(DB_NAME)
                            cur  = conn.cursor()
                            for _, row in df_upload.iterrows():
                                cod_r, desc_r, cc_r = str(row['Codigo']).strip(), str(row['Descricao']).strip(), str(row['CC']).strip()
                                qtd_r = pd.to_numeric(row['Quantidade'], errors='coerce')
                                if pd.isna(qtd_r) or qtd_r <= 0: continue
                                
                                cur.execute("INSERT OR IGNORE INTO centros_custo VALUES (?)", (cc_r,))
                                cur.execute("SELECT Quantidade FROM estoque WHERE Codigo=? AND CC=?", (cod_r, cc_r))
                                res = cur.fetchone()
                                if res:
                                    cur.execute("UPDATE estoque SET Quantidade=? WHERE Codigo=? AND CC=?", (res[0] + qtd_r, cod_r, cc_r))
                                else:
                                    cur.execute("INSERT INTO estoque VALUES (?,?,?,?)", (cod_r, desc_r, qtd_r, cc_r))
                            conn.commit()
                            conn.close()
                            st.success("✅ Importação concluída!")
                            st.cache_data.clear()
                            st.rerun()
                except Exception as e:
                    st.error(f"Erro: {e}")

        # ------------------------------------------
        # TAB 3: GERENCIAR SETORES E DE/PARA
        # ------------------------------------------
        with tab3:
            c_sec1, c_sec2 = st.columns(2)
            
            with c_sec1:
                st.subheader("➕ Novo Setor")
                novo_cc = st.text_input("Nome:")
                if st.button("Cadastrar"):
                    if novo_cc:
                        conn = sqlite3.connect(DB_NAME)
                        conn.execute("INSERT OR IGNORE INTO centros_custo VALUES (?)", (novo_cc,))
                        conn.commit(); conn.close()
                        st.success("Setor cadastrado!")
                        st.cache_data.clear()
                        st.rerun()
            
            with c_sec2:
                st.subheader("🔄 De/Para (Individual)")
                cc_antigo = st.selectbox("De:", lista_cc)
                cc_novo = st.text_input("Para (Novo Nome):")
                if st.button("Renomear Único"):
                    if cc_novo and cc_antigo:
                        conn = sqlite3.connect(DB_NAME)
                        conn.execute("INSERT OR IGNORE INTO centros_custo VALUES (?)", (cc_novo,))
                        conn.execute("UPDATE estoque SET CC = ? WHERE CC = ?", (cc_novo, cc_antigo))
                        conn.execute("DELETE FROM centros_custo WHERE nome = ?", (cc_antigo,))
                        conn.commit(); conn.close()
                        st.success("Setor renomeado!")
                        st.cache_data.clear()
                        st.rerun()

            st.divider()
            st.subheader("📂 De/Para em Massa")
            st.info("Suba uma planilha com as colunas **De** (Nome atual) e **Para** (Novo nome).")
            st.download_button("⬇️ Template De/Para", gerar_template_depara(), "template_depara.xlsx")
            arq_depara = st.file_uploader("Arquivo De/Para (.xlsx):", type=["xlsx"])
            
            if arq_depara and st.button("🚀 Processar De/Para em Massa"):
                df_dp = pd.read_excel(arq_depara)
                if 'De' in df_dp.columns and 'Para' in df_dp.columns:
                    conn = sqlite3.connect(DB_NAME)
                    for _, row in df_dp.iterrows():
                        de, para = str(row['De']).strip(), str(row['Para']).strip()
                        if de != 'nan' and para != 'nan':
                            conn.execute("INSERT OR IGNORE INTO centros_custo VALUES (?)", (para,))
                            conn.execute("UPDATE estoque SET CC = ? WHERE CC = ?", (para, de))
                            conn.execute("DELETE FROM centros_custo WHERE nome = ?", (de,))
                    conn.commit(); conn.close()
                    st.success("De/Para em massa concluído!")
                    st.cache_data.clear()
                    st.rerun()

        # ------------------------------------------
        # TAB 4: LIMPAR DADOS
        # ------------------------------------------
        with tab4:
            st.subheader("⚠️ Área de Risco")
            
            opcao = st.radio("Selecione a ação desejada:", [
                "1️⃣ Apenas zerar o estoque (Mantém os códigos salvos)", 
                "2️⃣ Excluir tudo (Limpa o banco de estoque e códigos)"
            ])
            
            senha_zerar = st.text_input("Senha Master:", type="password")
            
            if st.button("🚨 Confirmar Execução"):
                if senha_zerar == SENHA_ZERAR_ESTOQUE:
                    conn = sqlite3.connect(DB_NAME)
                    if "1️⃣" in opcao:
                        conn.execute("UPDATE estoque SET Quantidade = 0")
                        st.success("Quantidades zeradas com sucesso!")
                    else:
                        conn.execute("DELETE FROM estoque")
                        st.success("Todos os itens e códigos foram apagados!")
                    conn.commit()
                    conn.close()
                    st.cache_data.clear()
                    st.rerun()
                elif senha_zerar:
                    st.error("Senha master incorreta!")
