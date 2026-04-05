import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import sqlite3
import uuid
from datetime import datetime, timedelta
import io
import base64
import smtplib
from email.mime.text import MIMEText
import random

# 1. CONFIGURAÇÃO DA PÁGINA
st.set_page_config(page_title="Inventário Brastel", layout="wide", page_icon="📦")

# --- CONFIGURAÇÕES ABERTAS ---
ARQUIVO_PLANILHA = 'Almoxarifado.xlsm'
SENHA_ACESSO = "Almoxarifado"
SENHA_ZERAR_ESTOQUE = "admin123"
DB_NAME = 'estoque.db'
LIMITE_PESSOAS = 40
TEMPO_INATIVIDADE = 1

# --- CSS GLOBAL + RESPONSIVO ---
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Sora', sans-serif; }
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

# --- SISTEMA DE APROVAÇÃO POR E-MAIL (OUTLOOK) ---
def aprovar_acao_master(chave, descricao_acao):
    if f"token_{chave}" not in st.session_state:
        st.session_state[f"token_{chave}"] = None

    if st.button(f"📩 Solicitar Liberação: {descricao_acao}", key=f"req_{chave}"):
        codigo = str(random.randint(100000, 999999))
        st.session_state[f"token_{chave}"] = codigo
        
        remetente = "eduardo.sousa@brastelnet.com.br"
        senha_email = "Brastel25*"
        destinatario = "eduardo.sousa@brastelnet.com.br"
        
        msg = MIMEText(f"O administrador solicitou a seguinte ação no sistema:\n\nAÇÃO: {descricao_acao}\n\nPara autorizar, informe o código abaixo no sistema:\nCÓDIGO: {codigo}")
        msg['Subject'] = 'Aprovação de Sistema - Almoxarifado'
        msg['From'] = remetente
        msg['To'] = destinatario
        
        try:
            with smtplib.SMTP('smtp.office365.com', 587) as server:
                server.starttls()
                server.login(remetente, senha_email)
                server.sendmail(remetente, [destinatario], msg.as_string())
            st.info("✅ E-mail de autorização enviado para Eduardo Sousa - Controladoria! Solicite a ele o código gerado.")
        except Exception as e:
            st.error(f"Erro ao enviar e-mail. ({e})")

    if st.session_state[f"token_{chave}"]:
        token_input = st.text_input("🔑 Digite o Código de Autorização:", key=f"inp_{chave}")
        if st.button("✅ Confirmar Execução", key=f"exec_{chave}"):
            if token_input == st.session_state[f"token_{chave}"]:
                st.session_state[f"token_{chave}"] = None
                return True
            else:
                st.error("⛔ Código incorreto!")
    return False

# --- BANCO DE DADOS ---
def init_db():
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS estoque (Codigo TEXT, Descricao TEXT, Quantidade INTEGER, CC TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS acessos (sessao_id TEXT PRIMARY KEY, ultimo_clique TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS centros_custo (nome TEXT PRIMARY KEY)''')
init_db()

def carregar_estoque():
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        df = pd.read_sql_query("SELECT * FROM estoque", conn)
        if not df.empty:
            df['Quantidade'] = df['Quantidade'].astype(int)
    return df

@st.cache_data
def carregar_ccs():
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        df_cc = pd.read_sql_query("SELECT nome FROM centros_custo ORDER BY nome", conn)
        lista_cc = df_cc['nome'].tolist()
        if not lista_cc:
            try:
                df_bd = pd.read_excel(ARQUIVO_PLANILHA, sheet_name='BD', engine='openpyxl')
                lista_cc = df_bd['Centro de Custo'].dropna().unique().tolist()
                c = conn.cursor()
                for cc in lista_cc:
                    c.execute("INSERT OR IGNORE INTO centros_custo VALUES (?)", (cc,))
            except:
                lista_cc = ["Centro de Custo Geral"]
                c = conn.cursor()
                c.execute("INSERT OR IGNORE INTO centros_custo VALUES (?)", ("Centro de Custo Geral",))
    return lista_cc

def buscar_descricao_por_codigo(cod):
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        c = conn.cursor()
        c.execute("SELECT DISTINCT Descricao FROM estoque WHERE Codigo=?", (cod,))
        result = c.fetchone()
    return result[0] if result else None

def gerar_template_xlsx():
    template_df = pd.DataFrame({'Codigo': ['ABC001', 'ABC002'], 'Descricao': ['Parafuso M8', 'Cabo Elétrico 2,5mm'], 'Quantidade': [100, 50], 'CC': ['LIVRE DESTINAÇÃO', 'LIVRE DESTINAÇÃO']})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        template_df.to_excel(writer, index=False, sheet_name='Inventario')
    return buf.getvalue()

def gerar_template_depara():
    df = pd.DataFrame({'De': ['Centro de Custo Antigo 1', 'Centro de Custo Antigo 2'], 'Para': ['Centro de Custo Novo 1', 'Centro de Custo Novo 2']})
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

# --- CONTROLE DE ACESSO E LIMITE DE USUÁRIOS ---
if 'sessao_id' not in st.session_state:
    st.session_state.sessao_id = str(uuid.uuid4())

with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
    c = conn.cursor()
    tempo_limite = datetime.now() - timedelta(minutes=TEMPO_INATIVIDADE)
    c.execute("DELETE FROM acessos WHERE ultimo_clique < ?", (tempo_limite,))
    c.execute("INSERT OR REPLACE INTO acessos VALUES (?, ?)", (st.session_state.sessao_id, datetime.now()))
    c.execute("SELECT COUNT(*) FROM acessos")
    total_ativos = c.fetchone()[0]

if total_ativos > LIMITE_PESSOAS:
    st.error(f"⚠️ O sistema está lotado no momento ({total_ativos}/{LIMITE_PESSOAS} usuários). Por favor, tente novamente em 1 minuto.")
    st.stop()

# --- NAVEGAÇÃO ---
df = carregar_estoque()
lista_cc = carregar_ccs()

st.sidebar.title("Navegação")
menu = st.sidebar.radio("Ir para:", ["📊 Consulta", "🔒 Almoxarifado"])
st.sidebar.divider()
st.sidebar.markdown(f"🟢 **{total_ativos}/{LIMITE_PESSOAS}** pessoas online")

# ==========================================
# TELA 1: CONSULTA
# ==========================================
if menu == "📊 Consulta":
    src1 = logo_para_base64("logo1.png")
    src2 = logo_para_base64("logo2.png")
    img1 = f'<img class="img-logo1" src="{src1}">' if src1 else '<span style="color:#102a43;font-weight:700;">LOGO 1</span>'
    img2 = f'<img class="img-logo2" src="{src2}">' if src2 else '<span style="color:#102a43;font-weight:700;">LOGO 2</span>'
    
    df_ativos = df[df['Quantidade'] > 0]
    total_pecas   = f"{df_ativos['Quantidade'].sum():.0f}" if not df_ativos.empty else "0"
    total_itens   = str(df_ativos['Codigo'].nunique())     if not df_ativos.empty else "0"
    total_cc      = str(df_ativos['CC'].nunique())         if not df_ativos.empty else "0"

    components.html(f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
      * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Sora', sans-serif; }}
      .header-container {{ display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; padding: 20px 32px; border-radius: 16px; margin-bottom: 16px; background: linear-gradient(135deg, #f0f4f8 0%, #d9e2ec 100%); box-shadow: 0 4px 12px rgba(0,0,0,0.05); border: 1px solid #e2e8f0; }}
      .left-logo {{ justify-self: start; display: flex; align-items: center; }}
      .title-box {{ text-align: center; padding: 0 20px; }}
      .right-logo {{ justify-self: end; display: flex; align-items: center; }}
      .img-logo1 {{ height: 85px; width: auto; max-width: 240px; object-fit: contain; mix-blend-mode: darken; }}
      .img-logo2 {{ height: 35px; width: auto; max-width: 120px; object-fit: contain; mix-blend-mode: darken; }}
      .title-box h1 {{ font-size: 1.8rem; font-weight: 700; color: #102a43; letter-spacing: 0.02em; line-height: 1.15; }}
      .title-box p {{ font-size: 0.75rem; color: #334e68; margin-top: 5px; font-weight: 600; letter-spacing: 0.22em; text-transform: uppercase; }}
      .metrics-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 4px; }}
      .metric-card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }}
      .metric-label {{ font-size: 0.78rem; color: #718096; font-weight: 600; margin-bottom: 4px; }}
      .metric-value {{ font-size: 1.9rem; font-weight: 700; color: #1a202c; line-height: 1.1; }}
    </style>
    </head>
    <body>
    <div class="header-container"><div class="left-logo">{img1}</div><div class="title-box"><h1>INVENTÁRIO BRASTEL</h1><p>ALMOXARIFADO</p></div><div class="right-logo">{img2}</div></div>
    <div class="metrics-grid">
      <div class="metric-card"><div class="metric-label">📦 Total de Peças</div><div class="metric-value">{total_pecas}</div></div>
      <div class="metric-card"><div class="metric-label">🏷️ Itens Únicos</div><div class="metric-value">{total_itens}</div></div>
      <div class="metric-card"><div class="metric-label">🏢 Centros de Custo</div><div class="metric-value">{total_cc}</div></div>
    </div>
    </body>
    </html>
    """, height=260, scrolling=False)

    st.divider()
    
    c_busca, c_filtro = st.columns([2, 1])
    busca = c_busca.text_input("🔍 Pesquisar Código ou Descrição:")
    cc_filtro = c_filtro.selectbox("🏢 Filtrar por Centro de Custo:", ["Todos"] + lista_cc)

    df_filt = df_ativos.copy() 
    
    if cc_filtro != "Todos":
        df_filt = df_filt[df_filt['CC'] == cc_filtro]
        
    if busca:
        df_filt = df_filt[df_filt['Codigo'].astype(str).str.contains(busca, case=False) | df_filt['Descricao'].str.contains(busca, case=False, na=False)]
        
    st.dataframe(df_filt, use_container_width=True, hide_index=True)

# ==========================================
# TELA 2: ALMOXARIFADO
# ==========================================
else:
    st.title("🔒 Área Restrita — Almoxarifado")
    senha = st.text_input("Senha:", type="password")

    if senha == SENHA_ACESSO or senha == SENHA_ZERAR_ESTOQUE:
        abas_nomes = ["📝 Registro Individual", "📤 Carga em Massa"]
        if senha == SENHA_ZERAR_ESTOQUE:
            abas_nomes.extend(["🗑️ Excluir Item (Master)", "🏢 Gerenciar CCs (Master)", "⚠️ Limpar Dados (Master)"])

        abas = st.tabs(abas_nomes)

        # TAB 1: REGISTRO INDIVIDUAL
        with abas[0]:
            with st.form("registro", clear_on_submit=True):
                c1, c2 = st.columns(2)
                cod        = c1.text_input("Código:")
                desc_input = c2.text_input("Descrição (somente para itens novos):")
                c3, c4, c5 = st.columns([2, 2, 1])
                cc_sel = c3.selectbox("Centro de Custo:", lista_cc)
                op     = c4.selectbox("Operação:", ["Entrada", "Saída"])
                qtd    = c5.number_input("Qtd:", min_value=1, step=1, format="%d")

                if st.form_submit_button("✅ Confirmar"):
                    if not cod:
                        st.error("⛔ Informe o Código do item.")
                    else:
                        desc_existente = buscar_descricao_por_codigo(cod)
                        # Bloqueio de cadastro sem descrição e geração de alerta
                        if not desc_existente and not desc_input:
                            st.error("⛔ A Descrição é OBRIGATÓRIA para cadastrar um novo item no sistema.")
                        elif desc_existente and desc_input and desc_input.strip() != desc_existente.strip():
                            st.error(f"⛔ Conflito de Descrição! O código **{cod}** já está cadastrado com:\n\n**\"{desc_existente}\"**")
                        else:
                            desc_final = desc_existente if desc_existente else desc_input
                            with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                                cur = conn.cursor()
                                cur.execute("SELECT Quantidade FROM estoque WHERE Codigo=? AND CC=?", (cod, cc_sel))
                                res = cur.fetchone()
                                if res:
                                    if op == "Saída":
                                        # Sinal de falta de item
                                        if res[0] < qtd:
                                            st.error(f"⛔ FALTA DE ESTOQUE! O saldo atual é de apenas {res[0]} unidades. Não é possível realizar a saída.")
                                        else:
                                            cur.execute("UPDATE estoque SET Quantidade = Quantidade - ? WHERE Codigo=? AND CC=?", (qtd, cod, cc_sel))
                                            st.success(f"✅ Saída registrada. Saldo atualizado: {res[0] - qtd}")
                                            st.cache_data.clear()
                                    else:
                                        cur.execute("UPDATE estoque SET Quantidade = Quantidade + ? WHERE Codigo=? AND CC=?", (qtd, cod, cc_sel))
                                        st.success(f"✅ Entrada registrada. Saldo atualizado: {res[0] + qtd}")
                                        st.cache_data.clear()
                                else:
                                    if op == "Saída":
                                        st.error("⛔ ITEM NÃO ENCONTRADO! Não é possível realizar a saída de um item que não existe neste Centro de Custo.")
                                    else:
                                        cur.execute("INSERT INTO estoque VALUES (?,?,?,?)", (cod, desc_final, qtd, cc_sel))
                                        st.success("✅ Item novo cadastrado com sucesso.")
                                        st.cache_data.clear()

        # TAB 2: CARGA EM MASSA
        with abas[1]:
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
                            df_upload['Codigo'] = df_upload['Codigo'].astype(str).str.strip()
                            df_upload['Descricao'] = df_upload['Descricao'].astype(str).str.strip()
                            df_upload['CC'] = df_upload['CC'].astype(str).str.strip()
                            df_upload['Quantidade'] = pd.to_numeric(df_upload['Quantidade'], errors='coerce')
                            
                            df_upload = df_upload.dropna(subset=['Quantidade'])
                            df_upload = df_upload[df_upload['Quantidade'] > 0]
                            df_upload['Quantidade'] = df_upload['Quantidade'].astype(int)
                            df_upload = df_upload[(df_upload['Codigo'] != 'nan') & (df_upload['Codigo'] != '')]

                            ccs_arquivo = set(df_upload['CC'].unique())
                            ccs_existentes = set(lista_cc)
                            ccs_invalidos = ccs_arquivo - ccs_existentes

                            if ccs_invalidos:
                                st.error(f"⛔ IMPORTAÇÃO BLOQUEADA! Os seguintes Centros de Custo na planilha não existem no sistema: **{', '.join(ccs_invalidos)}**.\nCrie-os primeiro na aba Master ou corrija a planilha.")
                            else:
                                with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                                    cur = conn.cursor()
                                    cur.execute("SELECT Codigo, CC FROM estoque")
                                    db_set = set((row[0], row[1]) for row in cur.fetchall())

                                    inserts, updates = [], []

                                    for _, row in df_upload.iterrows():
                                        cod_r, desc_r, cc_r, qtd_r = row['Codigo'], row['Descricao'], row['CC'], row['Quantidade']
                                        
                                        if (cod_r, cc_r) not in db_set and (not desc_r or desc_r.lower() == 'nan'):
                                            continue
                                        
                                        if (cod_r, cc_r) in db_set:
                                            updates.append((qtd_r, cod_r, cc_r))
                                        else:
                                            inserts.append((cod_r, desc_r, qtd_r, cc_r))
                                            db_set.add((cod_r, cc_r))
                                    
                                    if inserts: cur.executemany("INSERT INTO estoque VALUES (?,?,?,?)", inserts)
                                    if updates: cur.executemany("UPDATE estoque SET Quantidade = Quantidade + ? WHERE Codigo=? AND CC=?", updates)
                                    
                                st.success(f"✅ Importação concluída! {len(inserts)} novos itens, {len(updates)} atualizações.")
                                st.cache_data.clear()
                                st.rerun()
                except Exception as e:
                    st.error(f"Erro: {e}")

        # ÁREA MASTER
        if senha == SENHA_ZERAR_ESTOQUE:
            with abas[2]:
                st.subheader("🗑️ Excluir Item do Banco")
                st.warning("Esta ação apagará o código e seu histórico de estoque de todos os CCs.")
                cod_excluir = st.text_input("Digite o Código do item que deseja apagar:")
                if cod_excluir and aprovar_acao_master("del_item", f"Excluir código {cod_excluir}"):
                    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT * FROM estoque WHERE Codigo=?", (cod_excluir,))
                        if cur.fetchone():
                            cur.execute("DELETE FROM estoque WHERE Codigo=?", (cod_excluir,))
                            st.success(f"✅ Registros do código **{cod_excluir}** apagados!")
                        else:
                            st.error("⛔ Código não encontrado.")
                        st.cache_data.clear()

            with abas[3]:
                c_sec1, c_sec2 = st.columns(2)
                with c_sec1:
                    st.subheader("➕ Novo Centro de Custo")
                    novo_cc = st.text_input("Nome:")
                    if novo_cc and aprovar_acao_master("new_cc", f"Criar Centro de Custo: {novo_cc}"):
                        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                            conn.execute("INSERT OR IGNORE INTO centros_custo VALUES (?)", (novo_cc,))
                        st.success("Centro de Custo cadastrado!")
                        st.cache_data.clear()
                        st.rerun()
                
                with c_sec2:
                    st.subheader("🔄 De/Para (Individual)")
                    cc_antigo = st.selectbox("De:", lista_cc)
                    cc_novo = st.text_input("Para (Novo Nome):")
                    if cc_novo and cc_antigo and aprovar_acao_master("rename_cc", f"Renomear {cc_antigo} para {cc_novo}"):
                        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                            conn.execute("INSERT OR IGNORE INTO centros_custo VALUES (?)", (cc_novo,))
                            conn.execute("UPDATE estoque SET CC = ? WHERE CC = ?", (cc_novo, cc_antigo))
                            conn.execute("DELETE FROM centros_custo WHERE nome = ?", (cc_antigo,))
                        st.success("Centro de Custo renomeado!")
                        st.cache_data.clear()
                        st.rerun()

                st.divider()
                st.subheader("📂 De/Para em Massa")
                st.download_button("⬇️ Template De/Para", gerar_template_depara(), "template_depara.xlsx")
                arq_depara = st.file_uploader("Arquivo De/Para (.xlsx):", type=["xlsx"])
                
                if arq_depara and aprovar_acao_master("depara_massa", "Processar planilha De/Para em massa"):
                    df_dp = pd.read_excel(arq_depara)
                    if 'De' in df_dp.columns and 'Para' in df_dp.columns:
                        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                            for _, row in df_dp.iterrows():
                                de, para = str(row['De']).strip(), str(row['Para']).strip()
                                if de != 'nan' and para != 'nan':
                                    conn.execute("INSERT OR IGNORE INTO centros_custo VALUES (?)", (para,))
                                    conn.execute("UPDATE estoque SET CC = ? WHERE CC = ?", (para, de))
                                    conn.execute("DELETE FROM centros_custo WHERE nome = ?", (de,))
                        st.success("De/Para em massa concluído!")
                        st.cache_data.clear()
                        st.rerun()

            with abas[4]:
                st.subheader("⚠️ Área de Risco - Acesso Master")
                opcao = st.radio("Selecione a ação desejada:", [
                    "1️⃣ Apenas zerar o estoque (Mantém os códigos salvos)", 
                    "2️⃣ Excluir tudo (Limpa o banco de estoque e códigos)"
                ])
                
                if aprovar_acao_master("limpeza", f"Limpeza de Banco: {opcao}"):
                    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                        if "1️⃣" in opcao:
                            conn.execute("UPDATE estoque SET Quantidade = 0")
                            st.success("Quantidades zeradas com sucesso!")
                        else:
                            conn.execute("DELETE FROM estoque")
                            st.success("Todos os itens apagados do banco!")
                    st.cache_data.clear()
                    st.rerun()
