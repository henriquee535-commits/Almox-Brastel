import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
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
import gc

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

# --- CONEXÃO COM SUPABASE (BLINDADA) ---
@contextmanager
def get_conn():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
        conn.commit()  # ✅ COMMIT DENTRO DO CONTEXT MANAGER
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

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
                CREATE TABLE IF NOT EXISTS colaboradores (
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
            
            c.execute("SELECT count(*) as total FROM usuarios")
            if c.fetchone()['total'] == 0:
                c.execute('''
                    INSERT INTO usuarios (email, senha, nome, nivel, cc_permitido) 
                    VALUES (%s, %s, %s, %s, %s)
                ''', ('master@brastelnet.com.br', SENHA_ZERAR_ESTOQUE, 'Administrador', 'Master', 'Todos'))

init_db()

# ── helpers de validação e data ─────────────────────────────────────────────
if 'usuario_logado' not in st.session_state:
    st.session_state.usuario_logado = None

def aprovar_acao_master(chave, mensagem):
    return st.checkbox(f"Confirmo: {mensagem}", key=chave)

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

# ── gerador html levinho ───────────────────────────────────────────────────
@st.cache_data(show_spinner=False, max_entries=20, ttl=120)
def gerar_html_comprovante(req_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM movimentacoes WHERE id = %s", (req_id,))
            req = cur.fetchone()
            if not req:
                return None
            
            cur.execute('SELECT "Descricao" FROM estoque WHERE "Codigo" = %s LIMIT 1', (req['codigo_item'],))
            desc = cur.fetchone()
            descricao = desc['Descricao'] if desc else "Descrição não encontrada"

    dt_sol = ajustar_fuso_br(req['data_solicitacao'])
    dt_apr = ajustar_fuso_br(req['data_aprovacao'])
    titulo = "REQUISIÇÃO DE MATERIAL (RDM)" if req['tipo'] == 'RDM' else "ENTREGA DE MATERIAL (CGM)"
    
    logo_b64 = logo_para_base64("logo1.png") or logo_para_base64("logo1.jpg")
    img_tag = f'<img src="{logo_b64}" style="max-height: 80px; margin-bottom: 10px;">' if logo_b64 else '<h2>BRASTEL</h2>'

    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <title>Comprovante #{req['id']} - Brastel</title>
        <style>
            body {{ font-family: Arial, sans-serif; padding: 40px; color: #333; max-width: 800px; margin: auto; }}
            .header {{ text-align: center; border-bottom: 2px solid #333; padding-bottom: 20px; margin-bottom: 30px; }}
            .title {{ font-size: 22px; font-weight: bold; margin-bottom: 5px; }}
            .subtitle {{ font-size: 18px; font-weight: bold; }}
            .info-box {{ margin-bottom: 30px; line-height: 1.6; border: 1px solid #ddd; padding: 15px; border-radius: 8px; }}
            .signatures {{ margin-top: 80px; display: flex; justify-content: space-between; text-align: center; }}
            .sig-line {{ width: 45%; border-top: 1px solid #333; padding-top: 10px; font-weight: bold; }}
            @media print {{ body {{ padding: 0; }} .no-print {{ display: none; }} }}
        </style>
    </head>
    <body onload="window.print()">
        <div class="no-print" style="text-align: center; margin-bottom: 20px;">
            <button onclick="window.print()" style="padding: 10px 20px; font-size: 16px; cursor: pointer;">Imprimir Agora</button>
        </div>
        <div class="header">
            {img_tag}
            <div class="title">CONTROLE DE ALMOXARIFADO</div>
            <div class="subtitle">TERMO DE {titulo} - #{req['id']}</div>
        </div>
        
        <div class="info-box">
            <strong>Data da Solicitação:</strong> {dt_sol.strftime('%d/%m/%Y %H:%M')}<br>
            <strong>Data da Aprovação:</strong> {dt_apr.strftime('%d/%m/%Y %H:%M') if dt_apr else 'N/A'}<br>
            <strong>Centro de Custo:</strong> {req['cc_destino']}<br>
            <strong>Solicitante (Sistema):</strong> {req['solicitante_email']}<br>
            <strong>Autorizado/Designado para Retirada:</strong> {req['retirante_nome']}<br>
            <strong>Aprovado por:</strong> {req['aprovador_email']}
        </div>
        
        <div class="info-box">
            <h3 style="margin-top:0;">DETALHES DO ITEM:</h3>
            <strong>Código:</strong> {req['codigo_item']}<br>
            <strong>Descrição:</strong> {descricao}<br>
            <strong>Quantidade:</strong> {req['quantidade']} unidades
        </div>
        
        <div class="info-box" style="margin-top: 40px; border: none; padding: 0;">
            <h3 style="margin-bottom: 5px;">Ato de Entrega/Retirada:</h3>
            Data física: ____/____/20___ &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; Hora: ____:____
        </div>
        
        <div class="signatures">
            <div class="sig-line">Assinatura do Almoxarife</div>
            <div class="sig-line">Assinatura de {req['retirante_nome']}</div>
        </div>
    </body>
    </html>
    """
    return html_content

# ── caches otimizados ────────────────────────────────────────────────────────
@st.cache_data(ttl=120, max_entries=2)
def carregar_estoque():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('SELECT "Codigo", "Descricao", "Quantidade", "CC" FROM estoque')
            rows = c.fetchall()
    df = pd.DataFrame(rows, columns=['Codigo', 'Descricao', 'Quantidade', 'CC'])
    if not df.empty:
        df['Quantidade'] = pd.to_numeric(df['Quantidade'], downcast='integer')
        df['CC'] = df['CC'].astype('category')
    return df

@st.cache_data(ttl=120, max_entries=2)
def carregar_telefonia():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('SELECT "Numero","Conta","Operadora","Colaborador","CC","Status","Gestor" FROM telefonia ORDER BY "Conta","Numero"')
            rows = c.fetchall()
    cols = ['Numero', 'Conta', 'Operadora', 'Colaborador', 'CC', 'Status', 'Gestor']
    df_tel = pd.DataFrame(rows, columns=cols)
    if not df_tel.empty:
        for col in ['Conta', 'Operadora', 'CC', 'Status']:
            df_tel[col] = df_tel[col].astype('category')
    return df_tel

@st.cache_data
def carregar_ccs():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute("SELECT nome FROM centros_custo ORDER BY nome")
            rows = c.fetchall()
    lista_cc = [r['nome'] for r in rows]
    if not lista_cc:
        lista_cc = ["Geral"]
    return lista_cc

@st.cache_data
def carregar_colaboradores():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute("SELECT nome FROM colaboradores ORDER BY nome")
            rows = c.fetchall()
    return [r['nome'] for r in rows]

def buscar_descricao_por_codigo(cod):
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('SELECT DISTINCT "Descricao" FROM estoque WHERE "Codigo" = %s', (cod,))
            result = c.fetchone()
    return result['Descricao'] if result else None

# ── templates ─────────────────────────────────────────────────────────────
def gerar_template_xlsx():
    df = pd.DataFrame({'Codigo': ['ABC'], 'Descricao': ['Parafuso'], 'Quantidade': [100], 'CC': ['01/0001']})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()

def gerar_template_telefonia():
    df = pd.DataFrame({'Numero': ['(11) 99999-0001'], 'Conta': ['BRASTEL'], 'Operadora': ['Claro'], 'Colaborador': ['João'], 'CC': ['01/0001'], 'Status': ['Ativo'], 'Gestor': ['Gestor']})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()

def gerar_template_depara():
    df = pd.DataFrame({'De': ['CC_Velho1'], 'Para': ['CC_Novo1']})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()

# --- CONTROLE DE ACESSO ---
if 'sessao_id' not in st.session_state:
    st.session_state.sessao_id = str(uuid.uuid4())

with get_conn() as conn:
    with conn.cursor() as c:
        c.execute("DELETE FROM acessos WHERE ultimo_clique < %s", (datetime.now() - timedelta(minutes=TEMPO_INATIVIDADE),))
        c.execute("INSERT INTO acessos (sessao_id, ultimo_clique) VALUES (%s, %s) ON CONFLICT (sessao_id) DO UPDATE SET ultimo_clique = EXCLUDED.ultimo_clique", (st.session_state.sessao_id, datetime.now()))
        c.execute("SELECT COUNT(*) as total FROM acessos")
        total_ativos = c.fetchone()['total']

if total_ativos > LIMITE_PESSOAS:
    st.error(f"⚠️ O sistema está lotado ({total_ativos}/{LIMITE_PESSOAS} usuários). Tente novamente em 1 minuto.")
    st.stop()

# --- CARGA INICIAL ---
df = carregar_estoque()
df_tel = carregar_telefonia()
lista_cc = carregar_ccs()
lista_colabs = carregar_colaboradores()

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
# TELAS PÚBLICAS
# ══════════════════════════════════════════════════════════════════
if menu == "📊 Consulta":
    src1, src2 = logo_para_base64("logo1.png"), logo_para_base64("logo2.png")
    img1 = f'<img class="img-logo1" src="{src1}">' if src1 else '<b>LOGO 1</b>'
    img2 = f'<img class="img-logo2" src="{src2}">' if src2 else '<b>LOGO 2</b>'
    df_ativos = df[df['Quantidade'] > 0]
    
    components.html(f"""
    <!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
      * {{ box-sizing:border-box;margin:0;padding:0;font-family:'Sora',sans-serif; }}
      .header-container {{ display:grid;grid-template-columns:1fr auto 1fr;align-items:center;padding:20px 32px;border-radius:16px;margin-bottom:16px;background:linear-gradient(135deg,#f0f4f8 0%,#d9e2ec 100%);border:1px solid #e2e8f0; }}
      .left-logo {{ justify-self:start;display:flex; }} .right-logo {{ justify-self:end;display:flex; }}
      .img-logo1 {{ height:85px;max-width:240px;object-fit:contain;mix-blend-mode:darken; }}
      .img-logo2 {{ height:35px;max-width:120px;object-fit:contain;mix-blend-mode:darken; }}
      .title-box {{ text-align:center; }} .title-box h1 {{ font-size:1.8rem;color:#102a43; }} .title-box p {{ font-size:.75rem;color:#334e68;font-weight:600;letter-spacing:.22em; }}
      .metrics-grid {{ display:grid;grid-template-columns:repeat(3,1fr);gap:12px; }}
      .metric-card {{ background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:16px; }}
      .metric-label {{ font-size:.78rem;color:#718096;font-weight:600; }} .metric-value {{ font-size:1.9rem;font-weight:700;color:#1a202c; }}
    </style></head><body>
    <div class="header-container"><div class="left-logo">{img1}</div><div class="title-box"><h1>INVENTÁRIO BRASTEL</h1><p>ALMOXARIFADO</p></div><div class="right-logo">{img2}</div></div>
    <div class="metrics-grid">
      <div class="metric-card"><div class="metric-label">📦 Total de Peças</div><div class="metric-value">{df_ativos['Quantidade'].sum():.0f}</div></div>
      <div class="metric-card"><div class="metric-label">🏷️ Itens Únicos</div><div class="metric-value">{df_ativos['Codigo'].nunique()}</div></div>
      <div class="metric-card"><div class="metric-label">🏢 Centros de Custo</div><div class="metric-value">{df_ativos['CC'].nunique()}</div></div>
    </div></body></html>
    """, height=300)

    c_b, c_f = st.columns([2, 1])
    busca = c_b.text_input("🔍 Pesquisar Código ou Descrição:")
    cc_filtro = c_f.selectbox("🏢 Filtrar por Centro de Custo:", ["Todos"] + lista_cc)

    df_filt = df_ativos.copy()
    if cc_filtro != "Todos":
        df_filt = df_filt[df_filt['CC'] == cc_filtro]
    if busca:
        df_filt = df_filt[df_filt['Codigo'].astype(str).str.contains(busca, case=False) | df_filt['Descricao'].str.contains(busca, case=False, na=False)]

    if not df_filt.empty:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as writer:
            df_filt.to_excel(writer, index=False)
        st.download_button("📥 Baixar Excel", data=buf.getvalue(), file_name="Consulta.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.dataframe(df_filt, use_container_width=True, hide_index=True)

elif menu == "📱 Telefonia":
    src1, src2 = logo_para_base64("logo1.png"), logo_para_base64("logo2.png")
    img1 = f'<img class="img-logo1" src="{src1}">' if src1 else '<b>LOGO 1</b>'
    img2 = f'<img class="img-logo2" src="{src2}">' if src2 else '<b>LOGO 2</b>'
    
    components.html(f"""
    <!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
      * {{ box-sizing:border-box;margin:0;padding:0;font-family:'Sora',sans-serif; }}
      .header-container {{ display:grid;grid-template-columns:1fr auto 1fr;align-items:center;padding:20px 32px;border-radius:16px;margin-bottom:16px;background:linear-gradient(135deg,#e8f4f8 0%,#c8dfe8 100%);border:1px solid #b8d4e0; }}
      .left-logo {{ justify-self:start;display:flex; }} .right-logo {{ justify-self:end;display:flex; }}
      .img-logo1 {{ height:85px;max-width:240px;object-fit:contain;mix-blend-mode:darken; }}
      .img-logo2 {{ height:35px;max-width:120px;object-fit:contain;mix-blend-mode:darken; }}
      .title-box {{ text-align:center; }} .title-box h1 {{ font-size:1.8rem;color:#0d3d52; }} .title-box p {{ font-size:.75rem;color:#1a6080;font-weight:600;letter-spacing:.22em; }}
      .metrics-grid {{ display:grid;grid-template-columns:repeat(3,1fr);gap:12px; }}
      .metric-card {{ background:#fff;border:1px solid #b8d4e0;border-radius:12px;padding:16px; }}
      .metric-label {{ font-size:.78rem;color:#718096;font-weight:600; }} .metric-value {{ font-size:1.9rem;font-weight:700;color:#1a202c; }}
    </style></head><body>
    <div class="header-container"><div class="left-logo">{img1}</div><div class="title-box"><h1>TELEFONIA BRASTEL</h1><p>GESTÃO DE LINHAS</p></div><div class="right-logo">{img2}</div></div>
    <div class="metrics-grid">
      <div class="metric-card"><div class="metric-label">📱 Total de Linhas</div><div class="metric-value">{len(df_tel)}</div></div>
      <div class="metric-card"><div class="metric-label">✅ Linhas Ativas</div><div class="metric-value">{len(df_tel[df_tel['Status'] == 'Ativo']) if not df_tel.empty else 0}</div></div>
      <div class="metric-card"><div class="metric-label">🏢 Centros de Custo</div><div class="metric-value">{df_tel['CC'].nunique() if not df_tel.empty else 0}</div></div>
    </div></body></html>
    """, height=300)

    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    busca_tel = c1.text_input("🔍 Pesquisar Número ou Colaborador:")
    conta_filt = c2.selectbox("🏢 Conta:", ["Todas"] + CONTAS_TELEFONIA)
    status_filt = c3.selectbox("✅ Status:", ["Todos"] + STATUS_TELEFONIA)
    cc_filt_tel = c4.selectbox("📂 Centro de Custo:", ["Todos"] + lista_cc)

    df_tel_filt = df_tel.copy()
    if conta_filt != "Todas":
        df_tel_filt = df_tel_filt[df_tel_filt['Conta'] == conta_filt]
    if status_filt != "Todos":
        df_tel_filt = df_tel_filt[df_tel_filt['Status'] == status_filt]
    if cc_filt_tel != "Todos":
        df_tel_filt = df_tel_filt[df_tel_filt['CC'] == cc_filt_tel]
    if busca_tel:
        df_tel_filt = df_tel_filt[df_tel_filt['Numero'].astype(str).str.contains(busca_tel, case=False) | df_tel_filt['Colaborador'].astype(str).str.contains(busca_tel, case=False, na=False)]

    if not df_tel_filt.empty:
        buf2 = io.BytesIO()
        with pd.ExcelWriter(buf2, engine='openpyxl') as writer:
            df_tel_filt.to_excel(writer, index=False)
        st.download_button("📥 Baixar Excel", data=buf2.getvalue(), file_name="Telefonia.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
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
        cc_opcoes = lista_cc if user_ccs_str == 'Todos' else [c.strip() for c in user_ccs_str.split('|')]

        modulos_disp = ["🛒 Requisições (RDM/CGM)", "👤 Meu Perfil"]
        if user['nivel'] in ['Almoxarife', 'Master']:
            modulos_disp.extend(["📦 Gestão de Estoque", "📱 Telefonia", "📋 Carga por Colaborador"])
        if user['nivel'] == 'Master':
            modulos_disp.append("⚙️ Administração")

        modulo_ativo = st.radio("Módulo:", modulos_disp, horizontal=True)
        st.divider()

        # ---------------------------------------------------------
        # MÓDULO: MEU PERFIL
        # ---------------------------------------------------------
        if modulo_ativo == "👤 Meu Perfil":
            st.subheader("Configurações da Conta")
            st.write(f"**Nome:** {user['nome']}")
            st.write(f"**E-mail:** {user['email']}")
            st.write(f"**Nível de Acesso:** {user['nivel']}")
            
            with st.form("form_senha"):
                st.write("🔒 **Alterar Senha**")
                senha_atual = st.text_input("Senha Atual", type="password")
                nova_senha = st.text_input("Nova Senha", type="password")
                conf_senha = st.text_input("Confirmar Nova Senha", type="password")
                
                if st.form_submit_button("Atualizar Senha"):
                    if not senha_atual or not nova_senha or not conf_senha:
                        st.error("Preencha todos os campos.")
                    elif senha_atual != user['senha']:
                        st.error("A senha atual informada está incorreta.")
                    elif nova_senha != conf_senha:
                        st.error("A nova senha e a confirmação não coincidem.")
                    elif len(nova_senha) < 4:
                        st.error("A nova senha deve ter no mínimo 4 caracteres.")
                    else:
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute("UPDATE usuarios SET senha=%s WHERE id=%s", (nova_senha, user['id']))
                        st.session_state.usuario_logado['senha'] = nova_senha
                        st.success("✅ Senha atualizada com sucesso!")

        # ---------------------------------------------------------
        # MÓDULO 1: REQUISIÇÕES
        # ---------------------------------------------------------
        elif modulo_ativo == "🛒 Requisições (RDM/CGM)":
            abas_req = ["Nova Solicitação"]
            if user['nivel'] in ['Almoxarife', 'Master']:
                abas_req.append("✅ Aprovações Pendentes")
            tabs_req = st.tabs(abas_req)
            
            with tabs_req[0]:
                st.subheader("Formulário de Requisição/Devolução")
                if not lista_colabs:
                    st.warning("⚠️ Peça ao Master para cadastrar a lista de Colaboradores antes de fazer solicitações.")
                else:
                    with st.form("form_solicitacao", clear_on_submit=True):
                        tipo_req = st.radio("Tipo:", ["RDM (Retirar Material)", "CGM (Devolver Material)"], horizontal=True)
                        tipo_db = "RDM" if "RDM" in tipo_req else "CGM"
                        cc_req = st.selectbox("Centro de Custo:", cc_opcoes)
                        
                        col_s1, col_s2 = st.columns(2)
                        cod_req = col_s1.text_input("Código do Item:")
                        qtd_req = col_s2.number_input("Quantidade:", min_value=1, step=1)
                        
                        retirante = st.selectbox("Colaborador Responsável (Que irá receber/devolver):", lista_colabs)
                        
                        if st.form_submit_button("Enviar Solicitação"):
                            if not cod_req:
                                st.error("Preencha o código.")
                            else:
                                desc_valida = buscar_descricao_por_codigo(cod_req)
                                if not desc_valida:
                                    st.error(f"⛔ O código '{cod_req}' não está cadastrado no almoxarifado.")
                                else:
                                    pode_prosseguir = True
                                    if tipo_db == "RDM":
                                        df_disp = df[(df['Codigo'] == cod_req) & (df['CC'] == cc_req)]
                                        saldo = df_disp['Quantidade'].sum() if not df_disp.empty else 0
                                        if saldo < qtd_req:
                                            st.error(f"⛔ Saldo insuficiente. Estoque atual no CC {cc_req
