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
SENHA_ZERAR_ESTOQUE = "admin123" # Senha exclusiva para zerar estoque
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

/* ── HEADER ── (Fundo Claro) */
.header-container {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 20px 40px;
    background: linear-gradient(135deg, #f0f4f8 0%, #d9e2ec 100%);
    border-radius: 16px;
    margin-bottom: 20px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    border: 1px solid #e2e8f0;
    gap: 16px;
}

.header-logo-img {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
}

.header-logo-img img {
    height: 52px;
    max-width: 140px;
    object-fit: contain;
    mix-blend-mode: darken; /* Ajuste para fundo claro */
}

.header-title-block {
    text-align: center;
    flex: 1;
}

.header-title-block h1 {
    font-size: 1.85rem;
    font-weight: 700;
    color: #102a43;
    margin: 0;
    letter-spacing: 0.02em;
    line-height: 1.15;
    text-transform: uppercase;
}

.header-title-block p {
    font-size: 0.78rem;
    color: #334e68;
    margin: 5px 0 0 0;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    font-weight: 600;
}

.header-right {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 8px;
    flex-shrink: 0;
}

.header-badge {
    background: rgba(16, 42, 67, 0.1);
    border: 1px solid rgba(16, 42, 67, 0.2);
    color: #102a43;
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    white-space: nowrap;
}

/* ── MÉTRICAS ── */
.metrics-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin-bottom: 20px;
}

.metric-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 16px 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
}

.metric-card .metric-label {
    font-size: 0.78rem;
    color: #718096;
    font-weight: 600;
    letter-spacing: 0.04em;
    margin-bottom: 4px;
}

.metric-card .metric-value {
    font-size: 1.9rem;
    font-weight: 700;
    color: #1a202c;
    line-height: 1.1;
}

/* ── TABELA RESPONSIVA ── */
.table-wrapper {
    width: 100%;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    border-radius: 10px;
    border: 1px solid #e2e8f0;
}

/* ── BOTÕES ── */
.stButton > button {
    background: linear-gradient(135deg, #1a3a4a, #0d5c8a);
    color: white;
    border: none;
    border-radius: 8px;
    font-family: 'Sora', sans-serif;
    font-weight: 600;
    padding: 0.5rem 1.5rem;
    transition: opacity 0.2s;
}
.stButton > button:hover { opacity: 0.88; color: white; }

.stAlert { border-radius: 10px; }

/* ── RESPONSIVO CELULAR ── */
@media (max-width: 768px) {
    .header-container {
        flex-direction: column;
        align-items: center;
        padding: 16px 20px;
        gap: 12px;
        text-align: center;
    }
    .header-logo-img img { height: 40px; max-width: 110px; }
    .header-title-block h1 { font-size: 1.3rem; }
    .header-title-block p { font-size: 0.7rem; }
    .header-right { flex-direction: row; justify-content: center; width: 100%; }
    .metrics-grid { grid-template-columns: 1fr; gap: 8px; }
    .metric-card { padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; }
    .metric-card .metric-label { margin-bottom: 0; font-size: 0.82rem; }
    .metric-card .metric-value { font-size: 1.4rem; }
}
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
    
    # Se estiver vazio, tenta popular pela planilha na primeira vez
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
c.execute("INSERT OR REPLACE INTO acessos VALUES (?, ?)",
          (st.session_state.sessao_id, datetime.now()))
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

    LOGO_STYLE = "height:52px;max-width:140px;object-fit:contain;mix-blend-mode:darken;"
    img1 = f'<img src="{src1}" style="{LOGO_STYLE}">' if src1 else '<span style="color:#102a43;font-weight:700;">LOGO 1</span>'
    img2 = f'<img src="{src2}" style="{LOGO_STYLE}">' if src2 else '<span style="color:#102a43;font-weight:700;">LOGO 2</span>'

    # Retirar itens com estoque zerado para os cálculos e exibição
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
      .header-logo-img {{ display: flex; align-items: center; justify-content: center; flex-shrink: 0; }}
      .header-logo-img img {{ height: 52px; max-width: 140px; object-fit: contain; mix-blend-mode: darken; }}
      .header-title-block {{ text-align: center; flex: 1; padding: 0 16px; }}
      .header-title-block h1 {{ font-size: 1.8rem; font-weight: 700; color: #102a43; letter-spacing: 0.02em; text-transform: uppercase; line-height: 1.15; }}
      .header-title-block p {{ font-size: 0.75rem; color: #334e68; margin-top: 5px; font-weight: 600; letter-spacing: 0.22em; text-transform: uppercase; }}
      .header-right {{ display: flex; flex-direction: column; align-items: flex-end; gap: 8px; flex-shrink: 0; }}
      .header-badge {{ background: rgba(16,42,67,0.1); border: 1px solid rgba(16,42,67,0.2); color: #102a43; border-radius: 20px; padding: 4px 12px; font-size: 0.74rem; font-weight: 600; letter-spacing: 0.06em; white-space: nowrap; }}

      .metrics-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 4px; }}
      .metric-card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }}
      .metric-label {{ font-size: 0.78rem; color: #718096; font-weight: 600; margin-bottom: 4px; }}
      .metric-value {{ font-size: 1.9rem; font-weight: 700; color: #1a202c; line-height: 1.1; }}

      @media (max-width: 640px) {{
        .header-container {{ flex-direction: column; padding: 14px 16px; gap: 10px; text-align: center; }}
        .header-logo-img img {{ height: 38px; max-width: 110px; }}
        .header-title-block h1 {{ font-size: 1.2rem; }}
        .header-title-block p  {{ font-size: 0.68rem; letter-spacing: 0.14em; }}
        .header-right {{ align-items: center; flex-direction: row; justify-content: center; width: 100%; }}
        .metrics-grid {{ grid-template-columns: 1fr; gap: 8px; }}
        .metric-card  {{ padding: 10px 14px; display: flex; justify-content: space-between; align-items: center; }}
        .metric-label {{ margin-bottom: 0; font-size: 0.82rem; }}
        .metric-value {{ font-size: 1.35rem; }}
      }}
    </style>
    </head>
    <body>

    <div class="header-container">
      <div class="header-logo-img">{img1}</div>
      <div class="header-title-block">
        <h1>Inventário Brastel</h1>
        <p>Almoxarifado</p>
      </div>
      <div class="header-right">
        <div class="header-logo-img">{img2}</div>
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
    df_filt = df_ativos.copy() # Tabela filtrada já sem itens zerados
    if busca:
        df_filt = df_filt[
            df_filt['Codigo'].astype(str).str.contains(busca, case=False) |
            df_filt['Descricao'].str.contains(busca, case=False, na=False)
        ]

    st.markdown('<div class="table-wrapper">', unsafe_allow_html=True)
    st.dataframe(df_filt, use_container_width=True, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)

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
            "⚠️ Zerar Estoque"
        ])

        # ------------------------------------------
        # TAB 1: REGISTRO INDIVIDUAL
        # ------------------------------------------
        with tab1:
            st.subheader("Registrar Entrada / Saída")
            with st.form("registro"):
                c1, c2 = st.columns(2)
                cod        = c1.text_input("Código:")
                desc_input = c2.text_input("Descrição (somente para itens novos):")

                c3, c4, c5 = st.columns([2, 2, 1])
                cc_sel = c3.selectbox("Setor (Centro de Custo):", lista_cc)
                op     = c4.selectbox("Operação:", ["Entrada", "Saída"])
                qtd    = c5.number_input("Qtd:", min_value=1.0)

                submitted = st.form_submit_button("✅ Confirmar")

            if submitted:
                if not cod:
                    st.error("⛔ Informe o Código do item.")
                else:
                    desc_existente = buscar_descricao_por_codigo(cod)
                    if desc_existente and desc_input and desc_input.strip() != desc_existente.strip():
                        st.error(
                            f"⛔ Conflito de Descrição!\n\n"
                            f"O código **{cod}** já está cadastrado com:\n\n"
                            f"**\"{desc_existente}\"**\n\n"
                            f"Deixe 'Descrição' em branco para usar a existente, ou corrija o código."
                        )
                    else:
                        desc_final = desc_existente if desc_existente else desc_input
                        conn = sqlite3.connect(DB_NAME)
                        cur  = conn.cursor()
                        cur.execute(
                            "SELECT Quantidade FROM estoque WHERE Codigo=? AND CC=?",
                            (cod, cc_sel)
                        )
                        res = cur.fetchone()
                        if res:
                            novo = (res[0] + qtd) if op == "Entrada" else max(0, res[0] - qtd)
                            cur.execute(
                                "UPDATE estoque SET Quantidade=? WHERE Codigo=? AND CC=?",
                                (novo, cod, cc_sel)
                            )
                            st.success(f"✅ {op} de {qtd:.0f} unidades registrada. Saldo: {novo:.0f}")
                        else:
                            if op == "Saída":
                                st.warning("⚠️ Item não encontrado neste setor. Saída não registrada.")
                            else:
                                cur.execute(
                                    "INSERT INTO estoque VALUES (?,?,?,?)",
                                    (cod, desc_final, qtd, cc_sel)
                                )
                                st.success(f"✅ Item novo cadastrado com {qtd:.0f} unidades.")
                        conn.commit()
                        conn.close()
                        st.cache_data.clear()
                        st.rerun()

        # ------------------------------------------
        # TAB 2: CARGA EM MASSA
        # ------------------------------------------
        with tab2:
            st.subheader("📤 Importar Inventário em Massa")
            st.info(
                "Faça upload de um arquivo **Excel (.xlsx)** com as colunas:\n\n"
                "`Codigo` | `Descricao` | `Quantidade` | `CC`\n\n"
                "• A operação padrão é **Entrada** (soma ao estoque existente).\n"
                "• Se o código já existir com descrição diferente, a linha será **ignorada** e reportada."
            )
            st.download_button(
                "⬇️ Baixar Template XLSX",
                data=gerar_template_xlsx(),
                file_name="template_inventario.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            arquivo = st.file_uploader("Selecione o arquivo Excel (.xlsx):", type=["xlsx"], key="upload_massa")

            if arquivo:
                try:
                    df_upload = pd.read_excel(arquivo, engine='openpyxl')
                    colunas_necessarias = {'Codigo', 'Descricao', 'Quantidade', 'CC'}
                    colunas_faltando = colunas_necessarias - set(df_upload.columns)
                    if colunas_faltando:
                        st.error(f"⛔ Colunas ausentes: {', '.join(colunas_faltando)}")
                    else:
                        df_upload['Codigo']     = df_upload['Codigo'].astype(str).str.strip()
                        df_upload['Descricao']  = df_upload['Descricao'].astype(str).str.strip()
                        df_upload['CC']         = df_upload['CC'].astype(str).str.strip()
                        df_upload['Quantidade'] = pd.to_numeric(df_upload['Quantidade'], errors='coerce').fillna(0)
                        st.write(f"**{len(df_upload)} linhas encontradas.** Pré-visualização:")
                        st.dataframe(df_upload.head(10), use_container_width=True, hide_index=True)

                        if st.button("🚀 Processar Importação"):
                            conn = sqlite3.connect(DB_NAME)
                            cur  = conn.cursor()
                            ok, conflitos, ignorados = 0, [], []
                            for _, row in df_upload.iterrows():
                                cod_r  = row['Codigo']
                                desc_r = row['Descricao']
                                qtd_r  = float(row['Quantidade'])
                                cc_r   = row['CC']
                                
                                # Verifica se o setor CC existe. Se não, cadastra automaticamente.
                                cur.execute("INSERT OR IGNORE INTO centros_custo VALUES (?)", (cc_r,))
                                
                                cur.execute("SELECT DISTINCT Descricao FROM estoque WHERE Codigo=?", (cod_r,))
                                desc_db = cur.fetchone()
                                if desc_db and desc_db[0].strip() != desc_r.strip():
                                    conflitos.append({'Codigo': cod_r, 'Desc no Arquivo': desc_r, 'Desc no Sistema': desc_db[0]})
                                    continue
                                if qtd_r <= 0:
                                    ignorados.append(cod_r)
                                    continue
                                cur.execute("SELECT Quantidade FROM estoque WHERE Codigo=? AND CC=?", (cod_r, cc_r))
                                res = cur.fetchone()
                                if res:
                                    cur.execute("UPDATE estoque SET Quantidade=? WHERE Codigo=? AND CC=?",
                                                (res[0] + qtd_r, cod_r, cc_r))
                                else:
                                    cur.execute("INSERT INTO estoque VALUES (?,?,?,?)", (cod_r, desc_r, qtd_r, cc_r))
                                ok += 1
                            conn.commit()
                            conn.close()
                            st.success(f"✅ **{ok} itens** importados com sucesso!")
                            if conflitos:
                                st.warning(f"⚠️ {len(conflitos)} linha(s) ignorada(s) por conflito:")
                                st.dataframe(pd.DataFrame(conflitos), use_container_width=True, hide_index=True)
                            if ignorados:
                                st.info(f"ℹ️ {len(ignorados)} linha(s) com quantidade inválida ignorada(s).")
                            st.cache_data.clear()
                            st.rerun()
                except Exception as e:
                    st.error(f"Erro ao processar arquivo: {e}")

        # ------------------------------------------
        # TAB 3: GERENCIAR SETORES (C.C.)
        # ------------------------------------------
        with tab3:
            col_cc1, col_cc2 = st.columns(2)
            
            with col_cc1:
                st.subheader("➕ Cadastrar Novo Setor")
                novo_cc = st.text_input("Nome do novo Centro de Custo:")
                if st.button("Cadastrar Setor"):
                    if novo_cc:
                        conn = sqlite3.connect(DB_NAME)
                        conn.execute("INSERT OR IGNORE INTO centros_custo VALUES (?)", (novo_cc,))
                        conn.commit()
                        conn.close()
                        st.success(f"Setor '{novo_cc}' cadastrado com sucesso!")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.warning("Preencha o nome do setor.")

            with col_cc2:
                st.subheader("🔄 Renomear Setor (De/Para)")
                cc_antigo = st.selectbox("Setor Atual (De):", lista_cc)
                cc_novo = st.text_input("Novo Nome (Para):")
                if st.button("Renomear Setor"):
                    if cc_novo and cc_antigo:
                        conn = sqlite3.connect(DB_NAME)
                        # Insere o novo nome
                        conn.execute("INSERT OR IGNORE INTO centros_custo VALUES (?)", (cc_novo,))
                        # Atualiza todo o estoque para o novo CC
                        conn.execute("UPDATE estoque SET CC = ? WHERE CC = ?", (cc_novo, cc_antigo))
                        # Deleta o CC antigo
                        conn.execute("DELETE FROM centros_custo WHERE nome = ?", (cc_antigo,))
                        conn.commit()
                        conn.close()
                        st.success(f"Setor renomeado de '{cc_antigo}' para '{cc_novo}'!")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.warning("Preencha o novo nome do setor.")

        # ------------------------------------------
        # TAB 4: ZERAR ESTOQUE
        # ------------------------------------------
        with tab4:
            st.subheader("⚠️ Zerar Todo o Estoque")
            st.warning("Esta ação irá definir a quantidade de **todos os itens** do sistema para zero. Essa ação não pode ser desfeita.")
            
            senha_zerar = st.text_input("Senha Master para Zerar Estoque:", type="password")
            
            if st.button("🚨 Confirmar Zeramento Total"):
                if senha_zerar == SENHA_ZERAR_ESTOQUE:
                    conn = sqlite3.connect(DB_NAME)
                    conn.execute("UPDATE estoque SET Quantidade = 0")
                    conn.commit()
                    conn.close()
                    st.success("Estoque de todos os itens foi zerado com sucesso!")
                    st.cache_data.clear()
                    st.rerun()
                elif senha_zerar:
                    st.error("Senha master incorreta!")
