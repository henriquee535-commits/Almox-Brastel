import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
import uuid
from datetime import datetime, timedelta
import io
import base64
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import random
import re
import os
from fpdf import FPDF

# 1. CONFIGURAÇÃO DA PÁGINA
st.set_page_config(page_title="Inventário Brastel", layout="wide", page_icon="📦")

# --- CONFIGURAÇÕES ---
ARQUIVO_PLANILHA = 'Almoxarifado.xlsm'
SENHA_ACESSO = st.secrets.get("SENHA_ACESSO", "123")
SENHA_ZERAR_ESTOQUE = st.secrets.get("SENHA_ZERAR_ESTOQUE", "123")
DATABASE_URL = st.secrets["DATABASE_URL"]
LIMITE_PESSOAS = 40
TEMPO_INATIVIDADE = 1

CONTAS_TELEFONIA = ["ENGIA", "BRASTEL", "ATTRON"]
OPERADORAS_TELEFONIA = ["Claro", "Vivo", "TIM", "Oi", "Algar", "Nextel", "Outra"]
STATUS_TELEFONIA = ["Ativo", "Inativo"]

# --- CSS GLOBAL ---
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

# --- CONEXÃO COM SUPABASE ---
def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# --- BANCO DE DADOS ---
def init_db():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('''
                CREATE TABLE IF NOT EXISTS estoque (
                    id SERIAL PRIMARY KEY,
                    "Codigo" TEXT,
                    "Descricao" TEXT,
                    "Quantidade" INTEGER,
                    "CC" TEXT
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS acessos (
                    sessao_id TEXT PRIMARY KEY,
                    ultimo_clique TIMESTAMP
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS centros_custo (
                    nome TEXT PRIMARY KEY
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS logs_envio (
                    id SERIAL PRIMARY KEY,
                    data_envio DATE UNIQUE
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS telefonia (
                    id SERIAL PRIMARY KEY,
                    "Numero"        TEXT UNIQUE,
                    "Conta"         TEXT,
                    "Operadora"     TEXT,
                    "Colaborador"   TEXT,
                    "CC"            TEXT,
                    "Status"        TEXT DEFAULT 'Ativo',
                    "Gestor"        TEXT
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    senha TEXT NOT NULL,
                    nome TEXT NOT NULL,
                    nivel TEXT CHECK (nivel IN ('Leitor', 'Gestor', 'Almoxarife', 'Master')),
                    cc_permitido TEXT DEFAULT 'Todos'
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS movimentacoes (
                    id SERIAL PRIMARY KEY,
                    tipo TEXT CHECK (tipo IN ('RDM', 'CGM')),
                    cc_destino TEXT NOT NULL,
                    solicitante_email TEXT NOT NULL,
                    retirante_nome TEXT NOT NULL,
                    codigo_item TEXT NOT NULL,
                    quantidade INTEGER NOT NULL,
                    status TEXT DEFAULT 'Pendente' CHECK (status IN ('Pendente', 'Aprovado', 'Rejeitado')),
                    data_solicitacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    data_aprovacao TIMESTAMP,
                    aprovador_email TEXT
                )
            ''')
            
            c.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='telefonia' AND column_name='Localizacao'
            """)
            if c.fetchone():
                c.execute('ALTER TABLE telefonia RENAME COLUMN "Localizacao" TO "Gestor"')
            
            c.execute("SELECT count(*) as total FROM usuarios")
            if c.fetchone()['total'] == 0:
                c.execute('''
                    INSERT INTO usuarios (email, senha, nome, nivel, cc_permitido) 
                    VALUES (%s, %s, %s, %s, %s)
                ''', ('master@brastelnet.com.br', SENHA_ZERAR_ESTOQUE, 'Administrador', 'Master', 'Todos'))
                
        conn.commit()

init_db()

# ── helpers de validação ──────────────────────────────────────────────────────
if 'usuario_logado' not in st.session_state:
    st.session_state.usuario_logado = None

def formatar_numero(raw: str):
    digits = re.sub(r'\D', '', str(raw))
    if len(digits) == 11 and digits[2] == '9': 
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
    elif len(digits) == 10: 
        return f"({digits[:2]}) {digits[2:6]}-{digits[6:]}"
    return None

def realizar_login(email, senha):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM usuarios WHERE email=%s AND senha=%s", (email.strip().lower(), senha))
            user = cur.fetchone()
            if user:
                st.session_state.usuario_logado = user
                return True
    return False

def logout():
    st.session_state.usuario_logado = None
    st.rerun()

def ajustar_fuso_br(dt_obj):
    if dt_obj:
        return dt_obj - timedelta(hours=3)
    return None

# ── pdf blindado e performático ──────────────────────────────────────────────
def gerar_pdf_comprovante(req_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM movimentacoes WHERE id = %s", (req_id,))
            req = cur.fetchone()
            if not req: return None
            
            cur.execute('SELECT "Descricao" FROM estoque WHERE "Codigo" = %s LIMIT 1', (req['codigo_item'],))
            desc = cur.fetchone()
            descricao = desc['Descricao'] if desc else "Descrição não encontrada"

    dt_sol = ajustar_fuso_br(req['data_solicitacao'])
    dt_apr = ajustar_fuso_br(req['data_aprovacao'])

    pdf = FPDF()
    pdf.add_page()
    
    # Tentativa segura de carregar a Logo
    try:
        if os.path.exists("logo1.png"):
            pdf.image("logo1.png", x=10, y=8, w=40)
        elif os.path.exists("logo1.jpg"):
            pdf.image("logo1.jpg", x=10, y=8, w=40)
    except Exception:
        pass # Segue gerando o documento se a logo estiver corrompida
                
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, txt="BRASTEL - CONTROLE DE ALMOXARIFADO", ln=True, align='C')
    pdf.set_font("Arial", 'B', 12)
    titulo = "REQUISIÇÃO DE MATERIAL (RDM)" if req['tipo'] == 'RDM' else "ENTREGA DE MATERIAL (CGM)"
    pdf.cell(0, 10, txt=f"TERMO DE {titulo} - #{req['id']}", ln=True, align='C')
    pdf.ln(10)
    
    pdf.set_font("Arial", '', 12)
    pdf.cell(200, 8, txt=f"Data da Solicitação: {dt_sol.strftime('%d/%m/%Y %H:%M')}", ln=True)
    pdf.cell(200, 8, txt=f"Data da Aprovação: {dt_apr.strftime('%d/%m/%Y %H:%M') if dt_apr else 'N/A'}", ln=True)
    pdf.cell(200, 8, txt=f"Centro de Custo: {req['cc_destino']}", ln=True)
    pdf.cell(200, 8, txt=f"Solicitante (Sistema): {req['solicitante_email']}", ln=True)
    pdf.cell(200, 8, txt=f"Autorizado/Designado para Retirada: {req['retirante_nome']}", ln=True)
    pdf.cell(200, 8, txt=f"Aprovado por: {req['aprovador_email']}", ln=True)
    
    pdf.ln(10)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(200, 8, txt="DETALHES DO ITEM:", ln=True)
    pdf.set_font("Arial", '', 12)
    pdf.cell(200, 8, txt=f"Código: {req['codigo_item']}", ln=True)
    pdf.cell(200, 8, txt=f"Descrição: {descricao}", ln=True)
    pdf.cell(200, 8, txt=f"Quantidade: {req['quantidade']} unidades", ln=True)
    
    pdf.ln(20)
    pdf.cell(200, 8, txt="Ato de Entrega/Retirada:", ln=True)
    pdf.cell(200, 8, txt="Data física: ____/____/20___   Hora: ____:____", ln=True)
    
    pdf.ln(20)
    pdf.cell(90, 8, txt="______________________________________", ln=False, align='C')
    pdf.cell(90, 8, txt="______________________________________", ln=True, align='C')
    pdf.cell(90, 8, txt="Assinatura do Almoxarife", ln=False, align='C')
    pdf.cell(90, 8, txt=f"Assinatura de {req['retirante_nome']}", ln=True, align='C')

    # Trata a saída independentemente da versão do FPDF instalada
    out = pdf.output(dest='S')
    if isinstance(out, str):
        return out.encode('latin-1')
    return bytes(out)

# Cache vital para não gerar PDFs na memória repetidas vezes e travar a aplicação
@st.cache_data(show_spinner=False)
def gerar_pdf_cached(req_id):
    return gerar_pdf_comprovante(req_id)

# ── cache db ──────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def carregar_estoque():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('SELECT "Codigo", "Descricao", "Quantidade", "CC" FROM estoque')
            rows = c.fetchall()
    df = pd.DataFrame(rows, columns=['Codigo', 'Descricao', 'Quantidade', 'CC'])
    if not df.empty:
        df['Quantidade'] = df['Quantidade'].astype(int)
    return df

@st.cache_data(ttl=300)
def carregar_telefonia():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('SELECT "Numero","Conta","Operadora","Colaborador","CC","Status","Gestor" FROM telefonia ORDER BY "Conta","Numero"')
            rows = c.fetchall()
    cols = ['Numero', 'Conta', 'Operadora', 'Colaborador', 'CC', 'Status', 'Gestor']
    return pd.DataFrame(rows, columns=cols)

@st.cache_data
def carregar_ccs():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute("SELECT nome FROM centros_custo ORDER BY nome")
            rows = c.fetchall()
    lista_cc = [r['nome'] for r in rows]
    if not lista_cc:
        lista_cc = ["Centro de Custo Geral"]
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute("INSERT INTO centros_custo (nome) VALUES (%s) ON CONFLICT DO NOTHING", ("Centro de Custo Geral",))
            conn.commit()
    return lista_cc

def buscar_descricao_por_codigo(cod):
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('SELECT DISTINCT "Descricao" FROM estoque WHERE "Codigo" = %s', (cod,))
            result = c.fetchone()
    return result['Descricao'] if result else None

def gerar_template_xlsx():
    template_df = pd.DataFrame({'Codigo': ['ABC001'], 'Descricao': ['Parafuso'], 'Quantidade': [100], 'CC': ['01/0001']})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        template_df.to_excel(writer, index=False, sheet_name='Inventario')
    return buf.getvalue()

def gerar_template_depara():
    df = pd.DataFrame({'De': ['CC Antigo'], 'Para': ['CC Novo']})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='DePara')
    return buf.getvalue()

def gerar_template_telefonia():
    df = pd.DataFrame({'Numero': ['(11) 99999-0001'], 'Conta': ['BRASTEL'], 'Operadora': ['Claro'], 'Colaborador': ['João'], 'CC': ['01/0001'], 'Status': ['Ativo'], 'Gestor': ['Gestor A']})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Telefonia')
    return buf.getvalue()

def logo_para_base64(path):
    for tentativa in [path, path.replace('.png', '.jpg'), path.replace('.png', '.jpeg')]:
        try:
            with open(tentativa, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            ext = tentativa.rsplit('.', 1)[-1].lower()
            mime = 'image/png' if ext == 'png' else 'image/jpeg'
            return f"data:{mime};base64,{data}"
        except FileNotFoundError: continue
    return None

def aprovar_acao_master(chave, descricao_acao):
    if f"token_{chave}" not in st.session_state:
        st.session_state[f"token_{chave}"] = None

    email_solicitante = st.text_input("📧 Seu e-mail (para identificação):", key=f"email_{chave}")
    if st.button(f"📩 Solicitar Liberação: {descricao_acao}", key=f"req_{chave}"):
        if not email_solicitante:
            st.error("⛔ Informe seu e-mail antes de solicitar.")
            return False
        codigo = str(random.randint(100000, 999999))
        st.session_state[f"token_{chave}"] = codigo
        remetente, senha_email, destinatario = st.secrets["email"]["remetente"], st.secrets["email"]["senha"], st.secrets["email"]["destinatario"]
        msg = MIMEText(f"Solicitação:\nSOLICITANTE: {email_solicitante}\nAÇÃO: {descricao_acao}\nCÓDIGO: {codigo}")
        msg['Subject'], msg['From'], msg['To'] = 'Aprovação Master', remetente, destinatario
        try:
            with smtplib.SMTP('smtp.office365.com', 587) as server:
                server.starttls()
                server.login(remetente, senha_email)
                server.sendmail(remetente, [destinatario], msg.as_string())
            st.info("✅ Solicitação enviada!")
        except Exception as e: st.error(f"Erro: {e}")

    if st.session_state[f"token_{chave}"]:
        token_input = st.text_input("🔑 Código enviado para Controladoria:", key=f"inp_{chave}")
        if st.button("✅ Confirmar Execução", key=f"exec_{chave}"):
            if token_input == st.session_state[f"token_{chave}"]:
                st.session_state[f"token_{chave}"] = None
                return True
            else: st.error("⛔ Código incorreto!")
    return False

# --- CONTROLE DE ACESSO ---
if 'sessao_id' not in st.session_state:
    st.session_state.sessao_id = str(uuid.uuid4())

with get_conn() as conn:
    with conn.cursor() as c:
        tempo_limite = datetime.now() - timedelta(minutes=TEMPO_INATIVIDADE)
        c.execute("DELETE FROM acessos WHERE ultimo_clique < %s", (tempo_limite,))
        c.execute("INSERT INTO acessos (sessao_id, ultimo_clique) VALUES (%s, %s) ON CONFLICT (sessao_id) DO UPDATE SET ultimo_clique = EXCLUDED.ultimo_clique", (st.session_state.sessao_id, datetime.now()))
        c.execute("SELECT COUNT(*) as total FROM acessos")
        total_ativos = c.fetchone()['total']
    conn.commit()

if total_ativos > LIMITE_PESSOAS:
    st.error(f"⚠️ O sistema está lotado ({total_ativos}/{LIMITE_PESSOAS} usuários). Tente novamente em 1 minuto.")
    st.stop()

# --- CARGA INICIAL ---
df       = carregar_estoque()
df_tel   = carregar_telefonia()
lista_cc = carregar_ccs()

# --- NAVEGAÇÃO LATERAL ---
st.sidebar.title("Navegação")
menu = st.sidebar.radio("Ir para:", ["📊 Consulta", "📱 Telefonia", "🔒 Sistema Interno"])
st.sidebar.divider()

if st.session_state.usuario_logado:
    st.sidebar.success(f"👤 Olá, {st.session_state.usuario_logado['nome']}")
    st.sidebar.caption(f"Nível: {st.session_state.usuario_logado['nivel']}")
    if st.sidebar.button("Sair / Logout"):
        logout()
st.sidebar.markdown(f"🟢 **{total_ativos}/{LIMITE_PESSOAS}** pessoas online")

# ══════════════════════════════════════════════════════════════════
# TELA 1: CONSULTA — ALMOXARIFADO 
# ══════════════════════════════════════════════════════════════════
if menu == "📊 Consulta":
    src1 = logo_para_base64("logo1.png")
    src2 = logo_para_base64("logo2.png")
    img1 = f'<img class="img-logo1" src="{src1}">' if src1 else '<span style="color:#102a43;font-weight:700;">LOGO 1</span>'
    img2 = f'<img class="img-logo2" src="{src2}">' if src2 else '<span style="color:#102a43;font-weight:700;">LOGO 2</span>'

    df_ativos   = df[df['Quantidade'] > 0]
    total_pecas = f"{df_ativos['Quantidade'].sum():.0f}" if not df_ativos.empty else "0"
    total_itens = str(df_ativos['Codigo'].nunique())     if not df_ativos.empty else "0"
    total_cc    = str(df_ativos['CC'].nunique())         if not df_ativos.empty else "0"

    components.html(f"""
    <!DOCTYPE html><html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
      * {{ box-sizing:border-box;margin:0;padding:0;font-family:'Sora',sans-serif; }}
      .header-container {{ display:grid;grid-template-columns:1fr auto 1fr;align-items:center;padding:20px 32px;border-radius:16px;margin-bottom:16px;background:linear-gradient(135deg,#f0f4f8 0%,#d9e2ec 100%);box-shadow:0 4px 12px rgba(0,0,0,.05);border:1px solid #e2e8f0; }}
      .left-logo {{ justify-self:start;display:flex;align-items:center; }}
      .title-box {{ text-align:center;padding:0 20px; }}
      .right-logo {{ justify-self:end;display:flex;align-items:center; }}
      .img-logo1 {{ height:85px;width:auto;max-width:240px;object-fit:contain;mix-blend-mode:darken; }}
      .img-logo2 {{ height:35px;width:auto;max-width:120px;object-fit:contain;mix-blend-mode:darken; }}
      .title-box h1 {{ font-size:1.8rem;font-weight:700;color:#102a43;letter-spacing:.02em;line-height:1.15; }}
      .title-box p  {{ font-size:.75rem;color:#334e68;margin-top:5px;font-weight:600;letter-spacing:.22em;text-transform:uppercase; }}
      .metrics-grid {{ display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:4px; }}
      .metric-card  {{ background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 20px;box-shadow:0 1px 4px rgba(0,0,0,.05); }}
      .metric-label {{ font-size:.78rem;color:#718096;font-weight:600;margin-bottom:4px; }}
      .metric-value {{ font-size:1.9rem;font-weight:700;color:#1a202c;line-height:1.1; }}
      @media(max-width:768px){{
        .header-container{{grid-template-columns:1fr;gap:15px;padding:15px;text-align:center;}}
        .left-logo,.right-logo{{justify-self:center;}}
        .img-logo1{{height:60px;}} .img-logo2{{height:30px;}}
        .title-box h1{{font-size:1.4rem;}}
        .metrics-grid{{grid-template-columns:1fr;gap:8px;}}
      }}
    </style>
    </head>
    <body>
    <div class="header-container">
      <div class="left-logo">{img1}</div>
      <div class="title-box"><h1>INVENTÁRIO BRASTEL</h1><p>ALMOXARIFADO</p></div>
      <div class="right-logo">{img2}</div>
    </div>
    <div class="metrics-grid">
      <div class="metric-card"><div class="metric-label">📦 Total de Peças</div><div class="metric-value">{total_pecas}</div></div>
      <div class="metric-card"><div class="metric-label">🏷️ Itens Únicos</div><div class="metric-value">{total_itens}</div></div>
      <div class="metric-card"><div class="metric-label">🏢 Centros de Custo</div><div class="metric-value">{total_cc}</div></div>
    </div>
    </body></html>
    """, height=350, scrolling=True)

    st.divider()

    c_busca, c_filtro = st.columns([2, 1])
    busca     = c_busca.text_input("🔍 Pesquisar Código ou Descrição:")
    cc_filtro = c_filtro.selectbox("🏢 Filtrar por Centro de Custo:", ["Todos"] + lista_cc)

    df_filt = df_ativos.copy()
    if cc_filtro != "Todos":
        df_filt = df_filt[df_filt['CC'] == cc_filtro]
    if busca:
        df_filt = df_filt[
            df_filt['Codigo'].astype(str).str.contains(busca, case=False) |
            df_filt['Descricao'].str.contains(busca, case=False, na=False)
        ]

    if not df_filt.empty:
        col_down, _ = st.columns([1, 4])
        buf_xlsx = io.BytesIO()
        with pd.ExcelWriter(buf_xlsx, engine='openpyxl') as writer:
            df_filt.to_excel(writer, index=False, sheet_name='Consulta_Inventario')
        col_down.download_button(
            label="📥 Baixar Excel",
            data=buf_xlsx.getvalue(),
            file_name=f"Consulta_Inventario_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    st.dataframe(df_filt, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════
# TELA 2: CONSULTA — TELEFONIA
# ══════════════════════════════════════════════════════════════════
elif menu == "📱 Telefonia":
    src1 = logo_para_base64("logo1.png")
    src2 = logo_para_base64("logo2.png")
    img1 = f'<img class="img-logo1" src="{src1}">' if src1 else '<span style="color:#102a43;font-weight:700;">LOGO 1</span>'
    img2 = f'<img class="img-logo2" src="{src2}">' if src2 else '<span style="color:#102a43;font-weight:700;">LOGO 2</span>'

    total_linhas = str(len(df_tel))
    total_ativas = str(len(df_tel[df_tel['Status'] == 'Ativo'])) if not df_tel.empty else "0"
    total_cc_tel = str(df_tel['CC'].nunique())                   if not df_tel.empty else "0"

    components.html(f"""
    <!DOCTYPE html><html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
      * {{ box-sizing:border-box;margin:0;padding:0;font-family:'Sora',sans-serif; }}
      .header-container {{ display:grid;grid-template-columns:1fr auto 1fr;align-items:center;padding:20px 32px;border-radius:16px;margin-bottom:16px;background:linear-gradient(135deg,#e8f4f8 0%,#c8dfe8 100%);box-shadow:0 4px 12px rgba(0,0,0,.05);border:1px solid #b8d4e0; }}
      .left-logo {{ justify-self:start;display:flex;align-items:center; }}
      .title-box {{ text-align:center;padding:0 20px; }}
      .right-logo {{ justify-self:end;display:flex;align-items:center; }}
      .img-logo1 {{ height:85px;width:auto;max-width:240px;object-fit:contain;mix-blend-mode:darken; }}
      .img-logo2 {{ height:35px;width:auto;max-width:120px;object-fit:contain;mix-blend-mode:darken; }}
      .title-box h1 {{ font-size:1.8rem;font-weight:700;color:#0d3d52;letter-spacing:.02em;line-height:1.15; }}
      .title-box p  {{ font-size:.75rem;color:#1a6080;margin-top:5px;font-weight:600;letter-spacing:.22em;text-transform:uppercase; }}
      .metrics-grid {{ display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:4px; }}
      .metric-card  {{ background:#fff;border:1px solid #b8d4e0;border-radius:12px;padding:16px 20px;box-shadow:0 1px 4px rgba(0,0,0,.05); }}
      .metric-label {{ font-size:.78rem;color:#718096;font-weight:600;margin-bottom:4px; }}
      .metric-value {{ font-size:1.9rem;font-weight:700;color:#1a202c;line-height:1.1; }}
      @media(max-width:768px){{
        .header-container{{grid-template-columns:1fr;gap:15px;padding:15px;text-align:center;}}
        .left-logo,.right-logo{{justify-self:center;}}
        .img-logo1{{height:60px;}} .img-logo2{{height:30px;}}
        .title-box h1{{font-size:1.4rem;}}
        .metrics-grid{{grid-template-columns:1fr;gap:8px;}}
      }}
    </style>
    </head>
    <body>
    <div class="header-container">
      <div class="left-logo">{img1}</div>
      <div class="title-box"><h1>TELEFONIA BRASTEL</h1><p>GESTÃO DE LINHAS</p></div>
      <div class="right-logo">{img2}</div>
    </div>
    <div class="metrics-grid">
      <div class="metric-card"><div class="metric-label">📱 Total de Linhas</div><div class="metric-value">{total_linhas}</div></div>
      <div class="metric-card"><div class="metric-label">✅ Linhas Ativas</div><div class="metric-value">{total_ativas}</div></div>
      <div class="metric-card"><div class="metric-label">🏢 Centros de Custo</div><div class="metric-value">{total_cc_tel}</div></div>
    </div>
    </body></html>
    """, height=350, scrolling=True)

    st.divider()

    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    busca_tel   = col1.text_input("🔍 Pesquisar Número ou Colaborador:")
    conta_filt  = col2.selectbox("🏢 Conta:", ["Todas"] + CONTAS_TELEFONIA)
    status_filt = col3.selectbox("✅ Status:", ["Todos"] + STATUS_TELEFONIA)
    cc_filt_tel = col4.selectbox("📂 Centro de Custo:", ["Todos"] + lista_cc)

    df_tel_filt = df_tel.copy()
    if conta_filt  != "Todas":
        df_tel_filt = df_tel_filt[df_tel_filt['Conta'] == conta_filt]
    if status_filt != "Todos":
        df_tel_filt = df_tel_filt[df_tel_filt['Status'] == status_filt]
    if cc_filt_tel != "Todos":
        df_tel_filt = df_tel_filt[df_tel_filt['CC'] == cc_filt_tel]
    if busca_tel:
        df_tel_filt = df_tel_filt[
            df_tel_filt['Numero'].astype(str).str.contains(busca_tel, case=False) |
            df_tel_filt['Colaborador'].astype(str).str.contains(busca_tel, case=False, na=False)
        ]

    if not df_tel_filt.empty:
        col_down2, _ = st.columns([1, 4])
        buf2 = io.BytesIO()
        with pd.ExcelWriter(buf2, engine='openpyxl') as writer:
            df_tel_filt.to_excel(writer, index=False, sheet_name='Consulta_Telefonia')
        col_down2.download_button(
            label="📥 Baixar Excel",
            data=buf2.getvalue(),
            file_name=f"Consulta_Telefonia_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    st.dataframe(df_tel_filt, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════
# TELA 3: SISTEMA INTERNO
# ══════════════════════════════════════════════════════════════════
else:
    if not st.session_state.usuario_logado:
        st.title("🔒 Acesso Restrito")
        with st.form("login_form"):
            email_login = st.text_input("E-mail Corporativo:")
            senha_login = st.text_input("Senha:", type="password")
            if st.form_submit_button("Entrar"):
                if realizar_login(email_login, senha_login):
                    st.success("Acesso liberado!")
                    st.rerun()
                else:
                    st.error("Credenciais inválidas.")
    else:
        user = st.session_state.usuario_logado
        st.title("Sistema Interno — Brastel")
        
        user_ccs_str = user['cc_permitido']
        if user_ccs_str == 'Todos':
            cc_opcoes = lista_cc
        else:
            cc_opcoes = [c.strip() for c in user_ccs_str.split('|')]

        modulos_disp = ["🛒 Requisições (RDM/CGM)"]
        if user['nivel'] in ['Almoxarife', 'Master']:
            modulos_disp.extend(["📦 Gestão de Estoque", "📱 Gestão de Telefonia"])
        if user['nivel'] == 'Master':
            modulos_disp.append("⚙️ Administração")

        modulo_ativo = st.radio("Selecione o Módulo:", modulos_disp, horizontal=True)
        st.divider()

        # ---------------------------------------------------------
        # MÓDULO 1: REQUISIÇÕES
        # ---------------------------------------------------------
        if modulo_ativo == "🛒 Requisições (RDM/CGM)":
            abas_req = ["Nova Solicitação"]
            if user['nivel'] in ['Almoxarife', 'Master']:
                abas_req.append("✅ Aprovações Pendentes")
            
            tabs_req = st.tabs(abas_req)
            
            with tabs_req[0]:
                st.subheader("Formulário de Requisição/Devolução")
                with st.form("form_solicitacao", clear_on_submit=True):
                    tipo_req = st.radio("Tipo:", ["RDM (Retirar Material)", "CGM (Devolver Material)"], horizontal=True)
                    tipo_db = "RDM" if "RDM" in tipo_req else "CGM"
                    cc_req = st.selectbox("Centro de Custo:", cc_opcoes)
                    
                    col_s1, col_s2 = st.columns(2)
                    cod_req = col_s1.text_input("Código do Item:")
                    qtd_req = col_s2.number_input("Quantidade:", min_value=1, step=1)
                    retirante = st.text_input("Nome da pessoa que irá buscar/entregar fisicamente:")
                    
                    if st.form_submit_button("Enviar Solicitação"):
                        if not cod_req or not retirante:
                            st.error("Preencha todos os campos obrigatórios.")
                        else:
                            # 1. Trava de Segurança Mestre (Bloqueia itens fantasmas)
                            desc_valida = buscar_descricao_por_codigo(cod_req)
                            
                            if not desc_valida:
                                st.error(f"⛔ O código '{cod_req}' não está cadastrado no sistema da empresa. Peça ao almoxarifado para registrar o item primeiro.")
                            else:
                                pode_prosseguir = True
                                
                                # 2. Validação de Saldo (RDM)
                                if tipo_db == "RDM":
                                    df_disp = df[(df['Codigo'] == cod_req) & (df['CC'] == cc_req)]
                                    saldo = df_disp['Quantidade'].sum() if not df_disp.empty else 0
                                    if saldo < qtd_req:
                                        st.error(f"⛔ Saldo insuficiente. Estoque atual no CC {cc_req}: {saldo} unidades.")
                                        pode_prosseguir = False
                                
                                if pode_prosseguir:
                                    with get_conn() as conn:
                                        with conn.cursor() as cur:
                                            cur.execute('''
                                                INSERT INTO movimentacoes (tipo, cc_destino, solicitante_email, retirante_nome, codigo_item, quantidade)
                                                VALUES (%s, %s, %s, %s, %s, %s)
                                            ''', (tipo_db, cc_req, user['email'], retirante, cod_req, qtd_req))
                                        conn.commit()
                                    st.success("✅ Solicitação enviada ao Almoxarifado com sucesso!")

            if len(abas_req) > 1:
                with tabs_req[1]:
                    st.subheader("Painel de Aprovações")
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT * FROM movimentacoes WHERE status = 'Pendente' ORDER BY data_solicitacao ASC")
                            pendentes = cur.fetchall()
                    
                    if not pendentes:
                        st.info("Nenhuma solicitação pendente no momento.")
                    else:
                        for req in pendentes:
                            with st.expander(f"[{req['tipo']}] Item: {req['codigo_item']} | Qtd: {req['quantidade']} | Req: {req['solicitante_email']}"):
                                st.write(f"**CC:** {req['cc_destino']} | **Retirante:** {req['retirante_nome']}")
                                dt_sol_ui = ajustar_fuso_br(req['data_solicitacao'])
                                st.write(f"**Data da Solicitação:** {dt_sol_ui.strftime('%d/%m/%Y %H:%M')}")
                                
                                c_btn1, c_btn2, _ = st.columns([1, 1, 3])
                                if c_btn1.button("✅ Aprovar e Liberar", key=f"apr_{req['id']}"):
                                    try:
                                        with get_conn() as conn:
                                            with conn.cursor() as cur:
                                                cur.execute("BEGIN;")
                                                if req['tipo'] == 'RDM':
                                                    cur.execute('SELECT "Quantidade" FROM estoque WHERE "Codigo"=%s AND "CC"=%s FOR UPDATE', (req['codigo_item'], req['cc_destino']))
                                                    saldo_db = cur.fetchone()
                                                    if not saldo_db or saldo_db['Quantidade'] < req['quantidade']:
                                                        st.error("Alerta: O estoque acabou antes da aprovação!")
                                                        cur.execute("ROLLBACK;")
                                                        st.stop()
                                                    cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" - %s WHERE "Codigo"=%s AND "CC"=%s', (req['quantidade'], req['codigo_item'], req['cc_destino']))
                                                else:
                                                    # Lógica CGM corrigida: Soma ou Insere se o CC não tiver o item
                                                    cur.execute('SELECT id FROM estoque WHERE "Codigo"=%s AND "CC"=%s', (req['codigo_item'], req['cc_destino']))
                                                    if cur.fetchone():
                                                        cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" + %s WHERE "Codigo"=%s AND "CC"=%s', (req['quantidade'], req['codigo_item'], req['cc_destino']))
                                                    else:
                                                        cur.execute('SELECT "Descricao" FROM estoque WHERE "Codigo"=%s LIMIT 1', (req['codigo_item'],))
                                                        desc_row = cur.fetchone()
                                                        cur.execute('INSERT INTO estoque ("Codigo", "Descricao", "Quantidade", "CC") VALUES (%s, %s, %s, %s)', (req['codigo_item'], desc_row['Descricao'], req['quantidade'], req['cc_destino']))
                                                
                                                cur.execute('''
                                                    UPDATE movimentacoes 
                                                    SET status = 'Aprovado', data_aprovacao = NOW(), aprovador_email = %s 
                                                    WHERE id = %s
                                                ''', (user['email'], req['id']))
                                                cur.execute("COMMIT;")
                                        st.success("✅ Movimentação aprovada e estoque atualizado!")
                                        st.cache_data.clear()
                                        st.rerun()
                                    except Exception as e: st.error(f"Erro na transação: {e}")

                                if c_btn2.button("❌ Rejeitar", key=f"rej_{req['id']}"):
                                    with get_conn() as conn:
                                        with conn.cursor() as cur:
                                            cur.execute("UPDATE movimentacoes SET status = 'Rejeitado', aprovador_email = %s WHERE id = %s", (user['email'], req['id']))
                                        conn.commit()
                                    st.warning("Solicitação rejeitada.")
                                    st.rerun()

                    st.divider()
                    st.subheader("🖨️ Documentos (Últimos Aprovados)")
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT id, tipo, data_aprovacao, retirante_nome FROM movimentacoes WHERE status = 'Aprovado' ORDER BY data_aprovacao DESC LIMIT 10")
                            aprovados = cur.fetchall()
                    
                    for ap in aprovados:
                        col_txt, col_dl = st.columns([3, 1])
                        dt_apr_ui = ajustar_fuso_br(ap['data_aprovacao'])
                        col_txt.write(f"#{ap['id']} - {ap['tipo']} - Retirante: {ap['retirante_nome']} (Aprovado em {dt_apr_ui.strftime('%d/%m %H:%M')})")
                        
                        # Função rodando em cache para não travar a UI
                        pdf_bytes = gerar_pdf_cached(ap['id'])
                        
                        if pdf_bytes:
                            col_dl.download_button(label="📄 Baixar PDF", data=pdf_bytes, file_name=f"Documento_{ap['tipo']}_{ap['id']}.pdf", mime="application/pdf", key=f"dl_pdf_{ap['id']}")

        # ---------------------------------------------------------
        # MÓDULO 2: ESTOQUE 
        # ---------------------------------------------------------
        elif modulo_ativo == "📦 Gestão de Estoque":
            abas_est = ["📝 Registro Individual", "📤 Carga em Massa"]
            tabs_est = st.tabs(abas_est)

            with tabs_est[0]:
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
                            if not desc_existente and not desc_input:
                                st.error("⛔ A Descrição é OBRIGATÓRIA para cadastrar um novo item.")
                            elif desc_existente and desc_input and desc_input.strip() != desc_existente.strip():
                                st.error(f"⛔ Conflito! O código **{cod}** já está cadastrado como:\n\n**\"{desc_existente}\"**")
                            else:
                                desc_final = desc_existente if desc_existente else desc_input
                                with get_conn() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute('SELECT "Quantidade" FROM estoque WHERE "Codigo"=%s AND "CC"=%s', (cod, cc_sel))
                                        res = cur.fetchone()
                                        if res:
                                            if op == "Saída":
                                                if res['Quantidade'] < qtd:
                                                    st.error(f"⛔ FALTA DE ESTOQUE! Saldo atual: {res['Quantidade']} unidades.")
                                                else:
                                                    cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" - %s WHERE "Codigo"=%s AND "CC"=%s', (qtd, cod, cc_sel))
                                                    st.success(f"✅ Saída registrada. Saldo: {res['Quantidade'] - qtd}")
                                                    st.cache_data.clear()
                                            else:
                                                cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" + %s WHERE "Codigo"=%s AND "CC"=%s', (qtd, cod, cc_sel))
                                                st.success(f"✅ Entrada registrada. Saldo: {res['Quantidade'] + qtd}")
                                                st.cache_data.clear()
                                        else:
                                            if op == "Saída":
                                                st.error("⛔ ITEM NÃO ENCONTRADO neste Centro de Custo.")
                                            else:
                                                cur.execute('INSERT INTO estoque ("Codigo", "Descricao", "Quantidade", "CC") VALUES (%s, %s, %s, %s)', (cod, desc_final, qtd, cc_sel))
                                                st.success("✅ Item novo cadastrado com sucesso.")
                                                st.cache_data.clear()
                                    conn.commit()

            with tabs_est[1]:
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
                                df_upload['Codigo']     = df_upload['Codigo'].astype(str).str.strip()
                                df_upload['Descricao']  = df_upload['Descricao'].astype(str).str.strip()
                                df_upload['CC']         = df_upload['CC'].astype(str).str.strip()
                                df_upload['Quantidade'] = pd.to_numeric(df_upload['Quantidade'], errors='coerce')
                                df_upload = df_upload.dropna(subset=['Quantidade'])
                                df_upload = df_upload[df_upload['Quantidade'] > 0]
                                df_upload['Quantidade'] = df_upload['Quantidade'].astype(int)
                                df_upload = df_upload[(df_upload['Codigo'] != 'nan') & (df_upload['Codigo'] != '')]

                                ccs_invalidos = set(df_upload['CC'].unique()) - set(lista_cc)
                                if ccs_invalidos:
                                    st.error(f"⛔ IMPORTAÇÃO BLOQUEADA! CCs não encontrados: **{', '.join(ccs_invalidos)}**.")
                                else:
                                    with get_conn() as conn:
                                        with conn.cursor() as cur:
                                            cur.execute('SELECT "Codigo", "CC" FROM estoque')
                                            db_set = set((r['Codigo'], r['CC']) for r in cur.fetchall())

                                            inserts, updates = [], []
                                            for _, row in df_upload.iterrows():
                                                cod_r  = row['Codigo']
                                                desc_r = row['Descricao']
                                                cc_r   = row['CC']
                                                qtd_r  = row['Quantidade']

                                                if (cod_r, cc_r) not in db_set and (not desc_r or desc_r.lower() == 'nan'): continue
                                                if (cod_r, cc_r) in db_set: updates.append((qtd_r, cod_r, cc_r))
                                                else:
                                                    inserts.append((cod_r, desc_r, qtd_r, cc_r))
                                                    db_set.add((cod_r, cc_r))

                                            if inserts: cur.executemany('INSERT INTO estoque ("Codigo","Descricao","Quantidade","CC") VALUES (%s,%s,%s,%s)', inserts)
                                            if updates: cur.executemany('UPDATE estoque SET "Quantidade" = "Quantidade" + %s WHERE "Codigo"=%s AND "CC"=%s', updates)
                                    conn.commit()
                                    st.success(f"✅ Importação concluída! {len(inserts)} novos, {len(updates)} atualizados.")
                                    st.cache_data.clear()
                                    st.rerun()
                    except Exception as e: st.error(f"Erro: {e}")

        # ---------------------------------------------------------
        # MÓDULO 3: TELEFONIA 
        # ---------------------------------------------------------
        elif modulo_ativo == "📱 Gestão de Telefonia":
            abas_tel = ["📱 Registro Individual", "📤 Carga em Massa"]
            tabs_tel = st.tabs(abas_tel)

            with tabs_tel[0]:
                st.subheader("Registrar / Editar Linha")
                acao_tel = st.radio("Operação:", ["➕ Nova Linha", "✏️ Editar Linha Existente", "🔄 Alterar Status"], horizontal=True)
                
                if acao_tel == "➕ Nova Linha":
                    with st.form("nova_linha", clear_on_submit=True):
                        tc1, tc2 = st.columns(2)
                        num_raw  = tc1.text_input("Número (ex: 11 99999-0001):")
                        conta_n  = tc2.selectbox("Conta:", CONTAS_TELEFONIA)
                        tc3, tc4 = st.columns(2)
                        oper_n   = tc3.selectbox("Operadora:", OPERADORAS_TELEFONIA)
                        colab_n  = tc4.text_input("Nome do Colaborador:")
                        tc5, tc6 = st.columns(2)
                        cc_n     = tc5.selectbox("Centro de Custo:", lista_cc)
                        loc_n    = tc6.text_input("Gestor:")

                        if st.form_submit_button("✅ Cadastrar Linha"):
                            num_fmt = formatar_numero(num_raw)
                            if not num_fmt: st.error("⛔ Número inválido.")
                            elif not colab_n.strip(): st.error("⛔ Informe o nome do colaborador.")
                            else:
                                with get_conn() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute('SELECT id FROM telefonia WHERE "Numero"=%s', (num_fmt,))
                                        if cur.fetchone(): st.error(f"⛔ O número **{num_fmt}** já está cadastrado.")
                                        else:
                                            cur.execute('INSERT INTO telefonia ("Numero","Conta","Operadora","Colaborador","CC","Status","Gestor") VALUES (%s,%s,%s,%s,%s,%s,%s)', (num_fmt, conta_n, oper_n, colab_n.strip(), cc_n, 'Ativo', loc_n.strip()))
                                            st.success(f"✅ Linha **{num_fmt}** cadastrada!")
                                            st.cache_data.clear()
                                    conn.commit()

                elif acao_tel == "✏️ Editar Linha Existente":
                    num_editar = st.text_input("Digite o número a editar:")
                    if num_editar:
                        num_fmt_ed = formatar_numero(num_editar)
                        if not num_fmt_ed: st.error("⛔ Formato inválido.")
                        else:
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    cur.execute('SELECT * FROM telefonia WHERE "Numero"=%s', (num_fmt_ed,))
                                    linha = cur.fetchone()
                            if not linha: st.warning("Número não encontrado.")
                            else:
                                with st.form("editar_linha"):
                                    ec1, ec2 = st.columns(2)
                                    idx_conta = CONTAS_TELEFONIA.index(linha['Conta']) if linha['Conta'] in CONTAS_TELEFONIA else 0
                                    idx_oper  = OPERADORAS_TELEFONIA.index(linha['Operadora']) if linha['Operadora'] in OPERADORAS_TELEFONIA else 0
                                    conta_e   = ec1.selectbox("Conta:", CONTAS_TELEFONIA, index=idx_conta)
                                    oper_e    = ec2.selectbox("Operadora:", OPERADORAS_TELEFONIA, index=idx_oper)
                                    ec3, ec4  = st.columns(2)
                                    colab_e   = ec3.text_input("Colaborador:", value=linha['Colaborador'] or "")
                                    loc_e     = ec4.text_input("Gestor:", value=linha['Gestor'] or "")
                                    idx_cc    = lista_cc.index(linha['CC']) if linha['CC'] in lista_cc else 0
                                    cc_e      = st.selectbox("Centro de Custo:", lista_cc, index=idx_cc)

                                    if st.form_submit_button("💾 Salvar Alterações"):
                                        with get_conn() as conn:
                                            with conn.cursor() as cur:
                                                cur.execute('UPDATE telefonia SET "Conta"=%s,"Operadora"=%s,"Colaborador"=%s,"CC"=%s,"Gestor"=%s WHERE "Numero"=%s', (conta_e, oper_e, colab_e.strip(), cc_e, loc_e.strip(), num_fmt_ed))
                                            conn.commit()
                                        st.success("✅ Linha atualizada!")
                                        st.cache_data.clear()

                else:  # Alterar Status
                    num_status = st.text_input("Digite o número:")
                    if num_status:
                        num_fmt_st = formatar_numero(num_status)
                        if not num_fmt_st: st.error("⛔ Formato inválido.")
                        else:
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    cur.execute('SELECT "Status" FROM telefonia WHERE "Numero"=%s', (num_fmt_st,))
                                    res_st = cur.fetchone()
                            if not res_st: st.warning("Número não encontrado.")
                            else:
                                st.info(f"Status atual: **{res_st['Status']}**")
                                novo_status = "Inativo" if res_st['Status'] == "Ativo" else "Ativo"
                                if st.button(f"🔄 Alterar para **{novo_status}**"):
                                    with get_conn() as conn:
                                        with conn.cursor() as cur:
                                            cur.execute('UPDATE telefonia SET "Status"=%s WHERE "Numero"=%s', (novo_status, num_fmt_st))
                                        conn.commit()
                                    st.success(f"✅ Status alterado!")
                                    st.cache_data.clear()
                                    st.rerun()

            with tabs_tel[1]:
                st.info("Upload de arquivo Excel Telefonia.")
                st.download_button("⬇️ Template Telefonia", gerar_template_telefonia(), "template_telefonia.xlsx")
                arq_tel = st.file_uploader("Arquivo de Telefonia (.xlsx):", type=["xlsx"], key="upload_tel")
                if arq_tel:
                    try:
                        df_tel_up = pd.read_excel(arq_tel, engine='openpyxl')
                        cols_req  = {'Numero', 'Conta', 'Operadora', 'Colaborador', 'CC', 'Status', 'Gestor'}
                        faltando  = cols_req - set(df_tel_up.columns)
                        if faltando: st.error(f"⛔ Colunas ausentes: {', '.join(faltando)}")
                        else:
                            if st.button("🚀 Processar Importação Telefonia"):
                                df_tel_up = df_tel_up.fillna('')
                                for col in df_tel_up.columns: df_tel_up[col] = df_tel_up[col].astype(str).str.strip()

                                erros, inserts_tel, updates_tel = [], [], []
                                with get_conn() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute('SELECT "Numero" FROM telefonia')
                                        nums_db = set(r['Numero'] for r in cur.fetchall())

                                        for i, row in df_tel_up.iterrows():
                                            num_fmt = formatar_numero(row['Numero'])
                                            if not num_fmt:
                                                erros.append(f"Linha {i+2}: número inválido '{row['Numero']}'")
                                                continue
                                            if row['Conta'] not in CONTAS_TELEFONIA:
                                                erros.append(f"Linha {i+2}: conta inválida '{row['Conta']}'")
                                                continue
                                            if row['CC'] not in lista_cc and row['CC'] != '':
                                                erros.append(f"Linha {i+2}: CC não encontrado '{row['CC']}'")
                                                continue
                                            
                                            status_excel = str(row['Status']).strip().capitalize()
                                            status_v = status_excel if status_excel in STATUS_TELEFONIA else 'Ativo'

                                            if num_fmt in nums_db: updates_tel.append((row['Conta'], row['Operadora'], row['Colaborador'], row['CC'], status_v, row['Gestor'], num_fmt))
                                            else:
                                                inserts_tel.append((num_fmt, row['Conta'], row['Operadora'], row['Colaborador'], row['CC'], status_v, row['Gestor']))
                                                nums_db.add(num_fmt)

                                        if inserts_tel: cur.executemany('INSERT INTO telefonia ("Numero","Conta","Operadora","Colaborador","CC","Status","Gestor") VALUES (%s,%s,%s,%s,%s,%s,%s)', inserts_tel)
                                        if updates_tel: cur.executemany('UPDATE telefonia SET "Conta"=%s,"Operadora"=%s,"Colaborador"=%s,"CC"=%s,"Status"=%s,"Gestor"=%s WHERE "Numero"=%s', updates_tel)
                                conn.commit()
                                if erros: st.warning("⚠️ Alguns registros foram ignorados:\n" + "\n".join(erros))
                                st.success(f"✅ Importação concluída! {len(inserts_tel)} novos, {len(updates_tel)} atualizados.")
                                st.cache_data.clear()
                                st.rerun()
                    except Exception as e: st.error(f"Erro: {e}")

        # ---------------------------------------------------------
        # MÓDULO 4: ADMINISTRAÇÃO 
        # ---------------------------------------------------------
        elif modulo_ativo == "⚙️ Administração":
            abas_admin = ["👥 Usuários", "🏢 Centros de Custo", "🗑️ Área de Exclusão"]
            tabs_admin = st.tabs(abas_admin)

            # TAB A: USUARIOS
            with tabs_admin[0]:
                st.subheader("Gestão de Acessos")
                with st.form("form_novo_usuario", clear_on_submit=True):
                    uc1, uc2 = st.columns(2)
                    novo_email = uc1.text_input("E-mail corporativo:")
                    nova_senha = uc2.text_input("Senha Inicial:", type="password")
                    
                    uc3, uc4 = st.columns(2)
                    nivel_acc = uc3.selectbox("Nível de Permissão:", ["Leitor", "Gestor", "Almoxarife", "Master"])
                    cc_acc = uc4.multiselect("Centros de Custo Permitidos:", ["Todos"] + lista_cc, default=["Todos"])
                    
                    if st.form_submit_button("Cadastrar Usuário"):
                        if novo_email and nova_senha:
                            cc_db_val = "Todos" if "Todos" in cc_acc else "|".join(cc_acc)
                            try:
                                with get_conn() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute('''
                                            INSERT INTO usuarios (email, senha, nome, nivel, cc_permitido) 
                                            VALUES (%s, %s, %s, %s, %s)
                                        ''', (novo_email.strip().lower(), nova_senha, novo_email.split('@')[0].capitalize(), nivel_acc, cc_db_val))
                                        conn.commit()
                                st.success("Usuário criado!")
                            except psycopg2.errors.UniqueViolation:
                                st.error("Este e-mail já possui cadastro.")
                        else: st.error("Preencha e-mail e senha.")
                
                st.divider()
                st.write("**Usuários Cadastrados:**")
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id, email, nome, nivel, cc_permitido FROM usuarios")
                        df_users = pd.DataFrame(cur.fetchall())
                st.dataframe(df_users, hide_index=True)

            # TAB B: CENTROS DE CUSTO
            with tabs_admin[1]:
                c_sec1, c_sec2 = st.columns(2)
                with c_sec1:
                    st.subheader("➕ Novo Centro de Custo")
                    novo_cc = st.text_input("Nome:")
                    if novo_cc and aprovar_acao_master("new_cc", f"Criar CC: {novo_cc}"):
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute("INSERT INTO centros_custo (nome) VALUES (%s) ON CONFLICT DO NOTHING", (novo_cc,))
                            conn.commit()
                        st.success("Centro de Custo cadastrado!")
                        st.cache_data.clear()
                        st.rerun()
                with c_sec2:
                    st.subheader("🔄 De/Para (Individual)")
                    cc_antigo = st.selectbox("De:", lista_cc)
                    cc_novo   = st.text_input("Para (Novo Nome):")
                    if cc_novo and cc_antigo and aprovar_acao_master("rename_cc", f"Renomear {cc_antigo} → {cc_novo}"):
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute("INSERT INTO centros_custo (nome) VALUES (%s) ON CONFLICT DO NOTHING", (cc_novo,))
                                cur.execute('UPDATE estoque   SET "CC" = %s WHERE "CC" = %s', (cc_novo, cc_antigo))
                                cur.execute('UPDATE telefonia SET "CC" = %s WHERE "CC" = %s', (cc_novo, cc_antigo))
                                cur.execute("DELETE FROM centros_custo WHERE nome = %s", (cc_antigo,))
                            conn.commit()
                        st.success("Renomeado com sucesso!")
                        st.cache_data.clear()
                        st.rerun()

                st.divider()
                st.subheader("📂 De/Para em Massa e Inclusão")
                c_up1, c_up2 = st.columns(2)
                with c_up1:
                    st.download_button("⬇️ Template De/Para", gerar_template_depara(), "template_depara.xlsx")
                    arq_depara = st.file_uploader("Arquivo De/Para (.xlsx):", type=["xlsx"])
                    if arq_depara and aprovar_acao_master("depara_massa", "Processar De/Para em massa"):
                        df_dp = pd.read_excel(arq_depara)
                        if 'De' in df_dp.columns and 'Para' in df_dp.columns:
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    for _, row in df_dp.iterrows():
                                        de, para = str(row['De']).strip(), str(row['Para']).strip()
                                        if de != 'nan' and para != 'nan':
                                            cur.execute("INSERT INTO centros_custo (nome) VALUES (%s) ON CONFLICT DO NOTHING", (para,))
                                            cur.execute('UPDATE estoque   SET "CC" = %s WHERE "CC" = %s', (para, de))
                                            cur.execute('UPDATE telefonia SET "CC" = %s WHERE "CC" = %s', (para, de))
                                            cur.execute("DELETE FROM centros_custo WHERE nome = %s", (de,))
                                conn.commit()
                            st.success("De/Para em massa concluído!")
                            st.cache_data.clear()
                            st.rerun()
                with c_up2:
                    ccs_massa = st.text_area("Lista de CCs (um por linha):")
                    if ccs_massa and aprovar_acao_master("add_cc_massa", "Adicionar CCs em Massa"):
                        novos_ccs = [c.strip() for c in ccs_massa.split('\n') if c.strip()]
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                for cc in novos_ccs: cur.execute("INSERT INTO centros_custo (nome) VALUES (%s) ON CONFLICT DO NOTHING", (cc,))
                            conn.commit()
                        st.success("Centros de Custo processados!")
                        st.cache_data.clear()
                        st.rerun()

            # TAB C: EXCLUSÕES
            with tabs_admin[2]:
                c_del1, c_del2 = st.columns(2)
                
                with c_del1:
                    st.subheader("🗑️ Excluir Item/Linha Específica")
                    cod_excluir = st.text_input("Código do Almoxarifado para apagar:")
                    if cod_excluir and aprovar_acao_master("del_item", f"Excluir código {cod_excluir}"):
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute('DELETE FROM estoque WHERE "Codigo"=%s', (cod_excluir,))
                            conn.commit()
                        st.success("Código apagado!")
                        st.cache_data.clear()
                        st.rerun()
                    
                    st.markdown("---")
                    num_del = st.text_input("Número de Telefone a excluir:")
                    if num_del:
                        num_del_fmt = formatar_numero(num_del)
                        if num_del_fmt and aprovar_acao_master("del_tel", f"Excluir linha {num_del_fmt}"):
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    cur.execute('DELETE FROM telefonia WHERE "Numero"=%s', (num_del_fmt,))
                                conn.commit()
                            st.success("Linha excluída!")
                            st.cache_data.clear()
                            st.rerun()

                with c_del2:
                    st.subheader("⚠️ Limpeza em Massa")
                    opcao_est = st.radio("Estoque:", ["Zerar quantidades", "Apagar todos os registros"])
                    if st.button("Executar Limpeza Estoque") and aprovar_acao_master("limp_est", f"Limpar {opcao_est}"):
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                if "Zerar" in opcao_est: cur.execute('UPDATE estoque SET "Quantidade" = 0')
                                else: cur.execute("DELETE FROM estoque")
                            conn.commit()
                        st.success("Estoque limpo!")
                        st.cache_data.clear()
                        st.rerun()
                    
                    st.markdown("---")
                    opcao_tel = st.radio("Telefonia:", ["Inativar todas", "Apagar todos os registros"])
                    if st.button("Executar Limpeza Telefonia") and aprovar_acao_master("limp_tel", f"Limpar {opcao_tel}"):
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                if "Inativar" in opcao_tel: cur.execute("UPDATE telefonia SET \"Status\" = 'Inativo'")
                                else: cur.execute("DELETE FROM telefonia")
                            conn.commit()
                        st.success("Telefonia limpa!")
                        st.cache_data.clear()
                        st.rerun()
