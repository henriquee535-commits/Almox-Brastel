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
                                            st.error(f"⛔ Saldo insuficiente. Estoque atual no CC {cc_req}: {saldo} unid.")
                                            pode_prosseguir = False
                                    
                                    if pode_prosseguir:
                                        with get_conn() as conn:
                                            with conn.cursor() as cur:
                                                cur.execute('''
                                                    INSERT INTO movimentacoes (tipo, cc_destino, solicitante_email, retirante_nome, codigo_item, quantidade)
                                                    VALUES (%s, %s, %s, %s, %s, %s)
                                                ''', (tipo_db, cc_req, user['email'], retirante, cod_req, qtd_req))
                                        st.success("✅ Solicitação enviada!")

            if len(abas_req) > 1:
                with tabs_req[1]:
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT * FROM movimentacoes WHERE status = 'Pendente' ORDER BY data_solicitacao ASC")
                            pendentes = cur.fetchall()
                    
                    if not pendentes:
                        st.info("Nenhuma solicitação pendente.")
                    else:
                        for req in pendentes:
                            with st.expander(f"[{req['tipo']}] Item: {req['codigo_item']} | Qtd: {req['quantidade']} | Resp: {req['retirante_nome']}"):
                                st.write(f"**CC:** {req['cc_destino']} | **Solicitante:** {req['solicitante_email']}")
                                c_btn1, c_btn2, _ = st.columns([1, 1, 3])
                                if c_btn1.button("✅ Aprovar", key=f"apr_{req['id']}"):
                                    try:
                                        with get_conn() as conn:
                                            with conn.cursor() as cur:
                                                cur.execute("BEGIN;")
                                                if req['tipo'] == 'RDM':
                                                    cur.execute('SELECT "Quantidade" FROM estoque WHERE "Codigo"=%s AND "CC"=%s FOR UPDATE', (req['codigo_item'], req['cc_destino']))
                                                    saldo_db = cur.fetchone()
                                                    if not saldo_db or saldo_db['Quantidade'] < req['quantidade']:
                                                        st.error("Alerta: Sem estoque!")
                                                        cur.execute("ROLLBACK;")
                                                        st.stop()
                                                    cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" - %s WHERE "Codigo"=%s AND "CC"=%s', (req['quantidade'], req['codigo_item'], req['cc_destino']))
                                                else:
                                                    cur.execute('SELECT id FROM estoque WHERE "Codigo"=%s AND "CC"=%s', (req['codigo_item'], req['cc_destino']))
                                                    if cur.fetchone():
                                                        cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" + %s WHERE "Codigo"=%s AND "CC"=%s', (req['quantidade'], req['codigo_item'], req['cc_destino']))
                                                    else:
                                                        cur.execute('SELECT "Descricao" FROM estoque WHERE "Codigo"=%s LIMIT 1', (req['codigo_item'],))
                                                        cur.execute('INSERT INTO estoque ("Codigo", "Descricao", "Quantidade", "CC") VALUES (%s, %s, %s, %s)', (req['codigo_item'], cur.fetchone()['Descricao'], req['quantidade'], req['cc_destino']))
                                                cur.execute("UPDATE movimentacoes SET status = 'Aprovado', data_aprovacao = NOW(), aprovador_email = %s WHERE id = %s", (user['email'], req['id']))
                                                cur.execute("COMMIT;")
                                        st.success("✅ Aprovado!")
                                        st.cache_data.clear()
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Erro: {e}")
                                if c_btn2.button("❌ Rejeitar", key=f"rej_{req['id']}"):
                                    with get_conn() as conn:
                                        with conn.cursor() as cur:
                                            cur.execute("UPDATE movimentacoes SET status = 'Rejeitado', aprovador_email = %s WHERE id = %s", (user['email'], req['id']))
                                    st.rerun()

                    st.divider()
                    st.subheader("🖨️ Documentos HTML")
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT id, tipo, data_aprovacao FROM movimentacoes WHERE status = 'Aprovado' ORDER BY data_aprovacao DESC LIMIT 10")
                            aprovados = cur.fetchall()
                    
                    for ap in aprovados:
                        col_txt, col_dl = st.columns([3, 1])
                        dt_apr = ajustar_fuso_br(ap['data_aprovacao'])
                        col_txt.write(f"#{ap['id']} - {ap['tipo']} (Aprovado em {dt_apr.strftime('%d/%m %H:%M')})")
                        
                        html_str = gerar_html_comprovante(ap['id'])
                        if html_str:
                            col_dl.download_button(
                                label="🖨️ Baixar Comprovante",
                                data=html_str,
                                file_name=f"Comprovante_{ap['tipo']}_{ap['id']}.html",
                                mime="text/html",
                                key=f"dl_html_{ap['id']}"
                            )

        # ---------------------------------------------------------
        # MÓDULO: CARGA POR COLABORADOR
        # ---------------------------------------------------------
        elif modulo_ativo == "📋 Carga por Colaborador":
            st.subheader("🎒 Rastreabilidade de Materiais em Posse")
            st.info("Calcula automaticamente o saldo físico de materiais com cada colaborador.")
            
            colab_alvo = st.selectbox("Selecione o Colaborador:", [""] + lista_colabs)
            
            if colab_alvo:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT 
                                m.codigo_item as "Código", 
                                MAX(e."Descricao") as "Descrição",
                                SUM(CASE WHEN m.tipo = 'RDM' THEN m.quantidade ELSE 0 END) - 
                                SUM(CASE WHEN m.tipo = 'CGM' THEN m.quantidade ELSE 0 END) as "Saldo em Mãos"
                            FROM movimentacoes m
                            LEFT JOIN estoque e ON m.codigo_item = e."Codigo"
                            WHERE m.status = 'Aprovado' AND m.retirante_nome = %s
                            GROUP BY m.codigo_item
                            HAVING (SUM(CASE WHEN m.tipo = 'RDM' THEN m.quantidade ELSE 0 END) - SUM(CASE WHEN m.tipo = 'CGM' THEN m.quantidade ELSE 0 END)) > 0
                        """, (colab_alvo,))
                        carga = cur.fetchall()
                
                if not carga:
                    st.success(f"✅ O colaborador **{colab_alvo}** não possui materiais pendentes de devolução.")
                else:
                    st.warning(f"⚠️ Materiais atualmente alocados para **{colab_alvo}**:")
                    st.dataframe(pd.DataFrame(carga), use_container_width=True, hide_index=True)
                    
                st.divider()
                st.markdown("#### Histórico de Movimentações")
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute('''
                            SELECT id, tipo as "Operação", codigo_item as "Código", quantidade as "Qtd", data_aprovacao as "Data Aprovação"
                            FROM movimentacoes 
                            WHERE status = 'Aprovado' AND retirante_nome = %s
                            ORDER BY data_aprovacao DESC
                        ''', (colab_alvo,))
                        hist = cur.fetchall()
                if hist:
                    df_h = pd.DataFrame(hist)
                    df_h['Data Aprovação'] = pd.to_datetime(df_h['Data Aprovação']).dt.tz_localize('UTC').dt.tz_convert('America/Sao_Paulo').dt.strftime('%d/%m/%Y %H:%M')
                    st.dataframe(df_h, hide_index=True)

        # ---------------------------------------------------------
        # MÓDULO 2: ESTOQUE 
        # ---------------------------------------------------------
        elif modulo_ativo == "📦 Gestão de Estoque":
            tabs_est = st.tabs(["📝 Registro Manual", "📤 Carga Excel"])
            with tabs_est[0]:
                with st.form("registro", clear_on_submit=True):
                    c1, c2 = st.columns(2)
                    cod, desc_input = c1.text_input("Código:"), c2.text_input("Descrição:")
                    c3, c4, c5 = st.columns([2, 2, 1])
                    cc_sel, op, qtd = c3.selectbox("Centro de Custo:", lista_cc), c4.selectbox("Operação:", ["Entrada", "Saída"]), c5.number_input("Qtd:", min_value=1, step=1)

                    if st.form_submit_button("✅ Confirmar"):
                        if not cod:
                            st.error("⛔ Informe o Código.")
                        else:
                            desc_existente = buscar_descricao_por_codigo(cod)
                            if not desc_existente and not desc_input:
                                st.error("⛔ Descrição obrigatória para item novo.")
                            elif desc_existente and desc_input and desc_input.strip() != desc_existente.strip():
                                st.error(f"⛔ Conflito! Código já é: {desc_existente}")
                            else:
                                with get_conn() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute('SELECT "Quantidade" FROM estoque WHERE "Codigo"=%s AND "CC"=%s', (cod, cc_sel))
                                        res = cur.fetchone()
                                        if res:
                                            if op == "Saída":
                                                if res['Quantidade'] < qtd:
                                                    st.error("⛔ FALTA DE ESTOQUE!")
                                                else:
                                                    cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" - %s WHERE "Codigo"=%s AND "CC"=%s', (qtd, cod, cc_sel))
                                                    st.success("✅ Saída registrada.")
                                            else:
                                                cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" + %s WHERE "Codigo"=%s AND "CC"=%s', (qtd, cod, cc_sel))
                                                st.success("✅ Entrada registrada.")
                                        else:
                                            if op == "Saída":
                                                st.error("⛔ ITEM NÃO ENCONTRADO.")
                                            else:
                                                cur.execute('INSERT INTO estoque ("Codigo", "Descricao", "Quantidade", "CC") VALUES (%s, %s, %s, %s)', (cod, desc_existente or desc_input, qtd, cc_sel))
                                                st.success("✅ Cadastrado com sucesso.")
                                st.cache_data.clear()

            with tabs_est[1]:
                st.download_button("⬇️ Template Inventário", gerar_template_xlsx(), "template.xlsx")
                arquivo = st.file_uploader("Arquivo Excel (.xlsx):", type=["xlsx"])
                if arquivo and st.button("🚀 Processar Importação"):
                    df_upload = pd.read_excel(arquivo, engine='openpyxl')
                    if {'Codigo', 'Descricao', 'Quantidade', 'CC'} - set(df_upload.columns):
                        st.error("Colunas inválidas.")
                    else:
                        df_upload['Codigo'] = df_upload['Codigo'].astype(str).str.strip()
                        df_upload['CC'] = df_upload['CC'].astype(str).str.strip()
                        df_upload['Quantidade'] = pd.to_numeric(df_upload['Quantidade'], errors='coerce')
                        df_upload = df_upload.dropna(subset=['Quantidade'])
                        
                        invalid_ccs = set(df_upload['CC'].unique()) - set(lista_cc)
                        if invalid_ccs:
                            st.error(f"⛔ CCs não encontrados: {', '.join(invalid_ccs)}")
                        else:
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    cur.execute('SELECT "Codigo", "CC" FROM estoque')
                                    db_set = set((r['Codigo'], r['CC']) for r in cur.fetchall())
                                    inserts, updates = [], []
                                    for _, row in df_upload.iterrows():
                                        if (row['Codigo'], row['CC']) in db_set:
                                            updates.append((row['Quantidade'], row['Codigo'], row['CC']))
                                        else:
                                            inserts.append((row['Codigo'], row['Descricao'], row['Quantidade'], row['CC']))
                                            db_set.add((row['Codigo'], row['CC']))
                                    if inserts:
                                        cur.executemany('INSERT INTO estoque ("Codigo","Descricao","Quantidade","CC") VALUES (%s,%s,%s,%s)', inserts)
                                    if updates:
                                        cur.executemany('UPDATE estoque SET "Quantidade" = "Quantidade" + %s WHERE "Codigo"=%s AND "CC"=%s', updates)
                            st.success("✅ Importação concluída!")
                            del df_upload, db_set
                            gc.collect()
                            st.cache_data.clear()
                            st.rerun()

        # ---------------------------------------------------------
        # MÓDULO 3: TELEFONIA 
        # ---------------------------------------------------------
        elif modulo_ativo == "📱 Telefonia":
            tabs_tel = st.tabs(["📱 Registro Individual", "📤 Carga em Massa"])
            with tabs_tel[0]:
                st.subheader("Registrar / Editar Linha")
                acao_tel = st.radio("Operação:", ["➕ Nova", "✏️ Editar", "🔄 Status"], horizontal=True)
                
                if acao_tel == "➕ Nova":
                    with st.form("nova_linha", clear_on_submit=True):
                        tc1, tc2 = st.columns(2)
                        num_raw = tc1.text_input("Número (ex: 11 99999-0001):")
                        conta_n = tc2.selectbox("Conta:", CONTAS_TELEFONIA)
                        tc3, tc4 = st.columns(2)
                        oper_n = tc3.selectbox("Operadora:", OPERADORAS_TELEFONIA)
                        colab_n = tc4.text_input("Nome do Colaborador:")
                        tc5, tc6 = st.columns(2)
                        cc_n = tc5.selectbox("Centro de Custo:", lista_cc)
                        loc_n = tc6.text_input("Gestor:")

                        if st.form_submit_button("✅ Cadastrar Linha"):
                            num_fmt = formatar_numero(num_raw)
                            if not num_fmt:
                                st.error("⛔ Número inválido.")
                            elif not colab_n.strip():
                                st.error("⛔ Informe o nome do colaborador.")
                            else:
                                with get_conn() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute('SELECT id FROM telefonia WHERE "Numero"=%s', (num_fmt,))
                                        if cur.fetchone():
                                            st.error(f"⛔ O número **{num_fmt}** já está cadastrado.")
                                        else:
                                            cur.execute('INSERT INTO telefonia ("Numero","Conta","Operadora","Colaborador","CC","Status","Gestor") VALUES (%s,%s,%s,%s,%s,%s,%s)', (num_fmt, conta_n, oper_n, colab_n.strip(), cc_n, 'Ativo', loc_n.strip()))
                                            st.success(f"✅ Linha **{num_fmt}** cadastrada!")
                                st.cache_data.clear()

                elif acao_tel == "✏️ Editar":
                    num_editar = st.text_input("Digite o número a editar:")
                    if num_editar:
                        num_fmt_ed = formatar_numero(num_editar)
                        if not num_fmt_ed:
                            st.error("⛔ Formato inválido.")
                        else:
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    cur.execute('SELECT * FROM telefonia WHERE "Numero"=%s', (num_fmt_ed,))
                                    linha = cur.fetchone()
                            if not linha:
                                st.warning("Número não encontrado.")
                            else:
                                with st.form("editar_linha"):
                                    ec1, ec2 = st.columns(2)
                                    idx_conta = CONTAS_TELEFONIA.index(linha['Conta']) if linha['Conta'] in CONTAS_TELEFONIA else 0
                                    idx_oper = OPERADORAS_TELEFONIA.index(linha['Operadora']) if linha['Operadora'] in OPERADORAS_TELEFONIA else 0
                                    conta_e = ec1.selectbox("Conta:", CONTAS_TELEFONIA, index=idx_conta)
                                    oper_e = ec2.selectbox("Operadora:", OPERADORAS_TELEFONIA, index=idx_oper)
                                    ec3, ec4 = st.columns(2)
                                    colab_e = ec3.text_input("Colaborador:", value=linha['Colaborador'] or "")
                                    loc_e = ec4.text_input("Gestor:", value=linha['Gestor'] or "")
                                    idx_cc = lista_cc.index(linha['CC']) if linha['CC'] in lista_cc else 0
                                    cc_e = st.selectbox("Centro de Custo:", lista_cc, index=idx_cc)

                                    if st.form_submit_button("💾 Salvar Alterações"):
                                        with get_conn() as conn:
                                            with conn.cursor() as cur:
                                                cur.execute('UPDATE telefonia SET "Conta"=%s,"Operadora"=%s,"Colaborador"=%s,"CC"=%s,"Gestor"=%s WHERE "Numero"=%s', (conta_e, oper_e, colab_e.strip(), cc_e, loc_e.strip(), num_fmt_ed))
                                        st.success("✅ Linha atualizada!")
                                        st.cache_data.clear()

                else:
                    num_status = st.text_input("Digite o número:")
                    if num_status:
                        num_fmt_st = formatar_numero(num_status)
                        if not num_fmt_st:
                            st.error("⛔ Formato inválido.")
                        else:
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    cur.execute('SELECT "Status" FROM telefonia WHERE "Numero"=%s', (num_fmt_st,))
                                    res_st = cur.fetchone()
                            if not res_st:
                                st.warning("Número não encontrado.")
                            else:
                                st.info(f"Status atual: **{res_st['Status']}**")
                                novo_status = "Inativo" if res_st['Status'] == "Ativo" else "Ativo"
                                if st.button(f"🔄 Alterar para **{novo_status}**"):
                                    with get_conn() as conn:
                                        with conn.cursor() as cur:
                                            cur.execute('UPDATE telefonia SET "Status"=%s WHERE "Numero"=%s', (novo_status, num_fmt_st))
                                    st.success(f"✅ Status alterado!")
                                    st.cache_data.clear()
                                    st.rerun()
            with tabs_tel[1]:
                st.download_button("⬇️ Template Telefonia", gerar_template_telefonia(), "tel.xlsx")
                arq_tel = st.file_uploader("Arquivo Telefonia (.xlsx):", type=["xlsx"])
                if arq_tel and st.button("🚀 Importar"):
                    df_tel_up = pd.read_excel(arq_tel, engine='openpyxl').fillna('')
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute('SELECT "Numero" FROM telefonia')
                            nums_db = set(r['Numero'] for r in cur.fetchall())
                            inserts_tel, updates_tel = [], []
                            for _, r in df_tel_up.iterrows():
                                num_fmt = formatar_numero(r['Numero'])
                                if num_fmt and r['Conta'] in CONTAS_TELEFONIA and (r['CC'] in lista_cc or r['CC'] == ''):
                                    st_v = str(r['Status']).capitalize() if str(r['Status']).capitalize() in STATUS_TELEFONIA else 'Ativo'
                                    if num_fmt in nums_db:
                                        updates_tel.append((r['Conta'], r['Operadora'], r['Colaborador'], r['CC'], st_v, r['Gestor'], num_fmt))
                                    else:
                                        inserts_tel.append((num_fmt, r['Conta'], r['Operadora'], r['Colaborador'], r['CC'], st_v, r['Gestor']))
                                        nums_db.add(num_fmt)
                            if inserts_tel:
                                cur.executemany('INSERT INTO telefonia ("Numero","Conta","Operadora","Colaborador","CC","Status","Gestor") VALUES (%s,%s,%s,%s,%s,%s,%s)', inserts_tel)
                            if updates_tel:
                                cur.executemany('UPDATE telefonia SET "Conta"=%s,"Operadora"=%s,"Colaborador"=%s,"CC"=%s,"Status"=%s,"Gestor"=%s WHERE "Numero"=%s', updates_tel)
                    st.success("✅ Importação concluída!")
                    del df_tel_up, nums_db
                    gc.collect()
                    st.cache_data.clear()
                    st.rerun()

        # ---------------------------------------------------------
        # MÓDULO 4: ADMINISTRAÇÃO 
        # ---------------------------------------------------------
        elif modulo_ativo == "⚙️ Administração":
            tabs_admin = st.tabs(["👥 Usuários", "🏢 Centros de Custo", "👷 Colaboradores", "🗑️ Exclusões"])

            with tabs_admin[0]:
                st.subheader("Gestão de Acessos")
                
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id, email, nome, nivel, cc_permitido FROM usuarios")
                        lista_usuarios = cur.fetchall()
                
                acao_usr = st.radio("Ação:", ["➕ Criar Novo", "✏️ Editar Existente", "🗑️ Excluir"], horizontal=True)
                
                if acao_usr == "➕ Criar Novo":
                    if st.button("🛠️ Corrigir Sequência de IDs do Banco"):
                        try:
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    cur.execute("SELECT setval('usuarios_id_seq', COALESCE((SELECT MAX(id)+1 FROM usuarios), 1), false);")
                            st.success("✅ Sequência de IDs corrigida! Tente criar o usuário novamente.")
                        except Exception as e:
                            st.error(f"Erro ao corrigir sequência: {e}")
                            
                    with st.form("form_usr"):
                        uc1, uc2 = st.columns(2)
                        n_email, n_senha = uc1.text_input("E-mail:"), uc2.text_input("Senha Inicial:", type="password")
                        uc3, uc4 = st.columns(2)
                        n_nivel = uc3.selectbox("Nível:", ["Leitor", "Gestor", "Almoxarife", "Master"])
                        n_cc = uc4.multiselect("CCs:", ["Todos"] + lista_cc, default=["Todos"])
                        
                        if st.form_submit_button("Criar Usuário") and n_email and n_senha:
                            try:
                                with get_conn() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute('INSERT INTO usuarios (email, senha, nome, nivel, cc_permitido) VALUES (%s,%s,%s,%s,%s)', 
                                                    (n_email.lower().strip(), n_senha, n_email.split('@')[0].capitalize(), n_nivel, "Todos" if "Todos" in n_cc else "|".join(n_cc)))
                                st.success("✅ Usuário criado!")
                                st.rerun()
                            except psycopg2.IntegrityError as e:
                                erro_real = str(e)
                                if "usuarios_email_key" in erro_real or "usuarios_email_unique" in erro_real:
                                    st.error("⛔ Este e-mail já existe de fato no banco de dados.")
                                elif "usuarios_pkey" in erro_real:
                                    st.error("⛔ Erro estrutural: Choque de ID. Clique no botão 'Corrigir Sequência' acima e tente novamente.")
                                elif "usuarios_nivel_check" in erro_real:
                                    st.error("⛔ O nível de acesso selecionado não é aceito pelo banco.")
                                else:
                                    st.error(f"⛔ Erro de Integridade Desconhecido:\n{erro_real}")
                            except Exception as e:
                                st.error(f"⛔ Erro inesperado: {e}")
                
                elif acao_usr == "✏️ Editar Existente":
                    usr_selecionado = st.selectbox("Selecione o Usuário:", [u['email'] for u in lista_usuarios])
                    if usr_selecionado:
                        usr_data = next(u for u in lista_usuarios if u['email'] == usr_selecionado)
                        with st.form("form_edit_usr"):
                            st.write(f"Editando permissões de: **{usr_data['nome']}**")
                            
                            idx_nivel = ["Leitor", "Gestor", "Almoxarife", "Master"].index(usr_data['nivel']) if usr_data['nivel'] in ["Leitor", "Gestor", "Almoxarife", "Master"] else 0
                            n_nivel = st.selectbox("Nível:", ["Leitor", "Gestor", "Almoxarife", "Master"], index=idx_nivel)

                            cc_atuais = [c for c in usr_data['cc_permitido'].split('|') if c in ["Todos"] + lista_cc]
                            if not cc_atuais:
                                cc_atuais = ["Todos"]
                            n_cc = st.multiselect("CCs:", ["Todos"] + lista_cc, default=cc_atuais)

                            if st.form_submit_button("💾 Salvar Alterações"):
                                cc_db_val = "Todos" if "Todos" in n_cc else "|".join(n_cc)
                                with get_conn() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute("UPDATE usuarios SET nivel=%s, cc_permitido=%s WHERE email=%s", (n_nivel, cc_db_val, usr_selecionado))
                                st.success("✅ Usuário atualizado!")
                                st.rerun()
                
                elif acao_usr == "🗑️ Excluir":
                    # Impede que o usuário master logado exclua a si mesmo
                    opcoes_exclusao = [u['email'] for u in lista_usuarios if u['email'] != user['email']]
                    
                    if not opcoes_exclusao:
                        st.info("Nenhum outro usuário disponível para exclusão.")
                    else:
                        usr_excluir = st.selectbox("Selecione o Usuário para Excluir:", opcoes_exclusao)
                        if st.button("🚨 Confirmar Exclusão") and usr_excluir:
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    cur.execute("DELETE FROM usuarios WHERE email=%s", (usr_excluir,))
                            st.success(f"✅ Usuário {usr_excluir} excluído com sucesso!")
                            st.rerun()
                                
                st.divider()
                st.write("**Usuários Cadastrados:**")
                st.dataframe(pd.DataFrame(lista_usuarios), hide_index=True)

            with tabs_admin[1]:
                c_sec1, c_sec2 = st.columns(2)
                with c_sec1:
                    st.subheader("➕ Novo Centro de Custo")
                    novo_cc = st.text_input("Nome:")
                    if novo_cc and aprovar_acao_master("new_cc", f"Criar CC: {novo_cc}"):
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute("INSERT INTO centros_custo (nome) VALUES (%s) ON CONFLICT DO NOTHING", (novo_cc,))
                        st.success("Centro de Custo cadastrado!")
                        st.cache_data.clear()
                        st.rerun()
                with c_sec2:
                    st.subheader("🔄 De/Para (Individual)")
                    cc_antigo = st.selectbox("De:", lista_cc)
                    cc_novo = st.text_input("Para (Novo Nome):")
                    if cc_novo and cc_antigo and aprovar_acao_master("rename_cc", f"Renomear {cc_antigo} → {cc_novo}"):
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute("INSERT INTO centros_custo (nome) VALUES (%s) ON CONFLICT DO NOTHING", (cc_novo,))
                                cur.execute('UPDATE estoque   SET "CC" = %s WHERE "CC" = %s', (cc_novo, cc_antigo))
                                cur.execute('UPDATE telefonia SET "CC" = %s WHERE "CC" = %s', (cc_novo, cc_antigo))
                                cur.execute("DELETE FROM centros_custo WHERE nome = %s", (cc_antigo,))
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
                            st.success("De/Para em massa concluído!")
                            st.cache_data.clear()
                            st.rerun()
                with c_up2:
                    ccs_massa = st.text_area("Lista de CCs (um por linha):")
                    if ccs_massa and aprovar_acao_master("add_cc_massa", "Adicionar CCs em Massa"):
                        novos_ccs = [c.strip() for c in ccs_massa.split('\n') if c.strip()]
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                for cc in novos_ccs:
                                    cur.execute("INSERT INTO centros_custo (nome) VALUES (%s) ON CONFLICT DO NOTHING", (cc,))
                        st.success("Centros de Custo processados!")
                        st.cache_data.clear()
                        st.rerun()

            with tabs_admin[2]:
                st.subheader("👷 Lista de Nomes para Retirada de Material")
                c_colab1, c_colab2 = st.columns(2)
                
                with c_colab1:
                    st.markdown("##### 👤 Inclusão Individual")
                    novo_colab = st.text_input("Cadastrar Novo Colaborador (Nome Completo):")
                    if st.button("➕ Adicionar Individual") and novo_colab:
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute("INSERT INTO colaboradores (nome) VALUES (%s) ON CONFLICT DO NOTHING", (novo_colab.strip().upper(),))
                        st.success(f"{novo_colab} adicionado!")
                        st.cache_data.clear()
                        st.rerun()

                    st.divider()

                    st.markdown("##### 📑 Inclusão em Massa (Copiar e Colar)")
                    lista_massa = st.text_area("Cole a lista de colaboradores (um nome por linha):", height=150)
                    if st.button("➕ Processar Inclusão em Massa") and lista_massa:
                        novos_colabs = [nome.strip().upper() for nome in lista_massa.split('\n') if nome.strip()]
                        
                        if novos_colabs:
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    cur.executemany(
                                        "INSERT INTO colaboradores (nome) VALUES (%s) ON CONFLICT DO NOTHING",
                                        [(nome,) for nome in novos_colabs]
                                    )
                            st.success(f"✅ {len(novos_colabs)} colaboradores processados e adicionados!")
                            st.cache_data.clear()
                            st.rerun()
                
                with c_colab2:
                    st.markdown("##### 🗑️ Remoção")
                    if lista_colabs:
                        del_colab = st.selectbox("Selecione para Remover:", lista_colabs)
                        if st.button("🗑️ Remover Colaborador"):
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    cur.execute("DELETE FROM colaboradores WHERE nome = %s", (del_colab,))
                            st.success(f"{del_colab} removido!")
                            st.cache_data.clear()
                            st.rerun()

            with tabs_admin[3]:
                c_del1, c_del2 = st.columns(2)
                
                with c_del1:
                    st.subheader("🗑️ Excluir Item/Linha Específica")
                    cod_excluir = st.text_input("Código do Almoxarifado para apagar:")
                    if cod_excluir and aprovar_acao_master("del_item", f"Excluir código {cod_excluir}"):
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute('DELETE FROM estoque WHERE "Codigo"=%s', (cod_excluir,))
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
                            st.success("Linha excluída!")
                            st.cache_data.clear()
                            st.rerun()

                with c_del2:
                    st.subheader("⚠️ Limpeza em Massa")
                    opcao_est = st.radio("Estoque:", ["Zerar quantidades", "Apagar todos os registros"])
                    if st.button("Executar Limpeza Estoque") and aprovar_acao_master("limp_est", f"Limpar {opcao_est}"):
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                if "Zerar" in opcao_est:
                                    cur.execute('UPDATE estoque SET "Quantidade" = 0')
                                else:
                                    cur.execute("DELETE FROM estoque")
                        st.success("Estoque limpo!")
                        st.cache_data.clear()
                        st.rerun()
                    
                    st.markdown("---")
                    opcao_tel = st.radio("Telefonia:", ["Inativar todas", "Apagar todos os registros"])
                    if st.button("Executar Limpeza Telefonia") and aprovar_acao_master("limp_tel", f"Limpar {opcao_tel}"):
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                if "Inativar" in opcao_tel:
                                    cur.execute("UPDATE telefonia SET \"Status\" = 'Inativo'")
                                else:
                                    cur.execute("DELETE FROM telefonia")
                        st.success("Telefonia limpa!")
                        st.cache_data.clear()
                        st.rerun()
