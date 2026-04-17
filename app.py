import streamlit as st
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
import random
import re
import gc

# ══════════════════════════════════════════════════════════════════════════════
# 1. CONFIGURAÇÃO DA PÁGINA E VARIÁVEIS GLOBAIS
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title="Inventário Brastel", layout="wide", page_icon="📦")

ARQUIVO_PLANILHA = 'Almoxarifado.xlsm'
SENHA_ACESSO = st.secrets.get("SENHA_ACESSO", "123")
SENHA_ZERAR_ESTOQUE = st.secrets.get("SENHA_ZERAR_ESTOQUE", "123")
DATABASE_URL = st.secrets["DATABASE_URL"]

# ── MELHORIA 3: CORREÇÃO SMTP ──
# Lê do bloco [email] do secrets.toml: remetente, senha, destinatario
EMAIL_USER = st.secrets.get("email", {}).get("remetente", st.secrets.get("EMAIL_USER", ""))
EMAIL_PASS = st.secrets.get("email", {}).get("senha", st.secrets.get("EMAIL_PASS", ""))
EMAIL_DEST = st.secrets.get("email", {}).get("destinatario", EMAIL_USER)

LIMITE_PESSOAS = 40
TEMPO_INATIVIDADE = 1
MAX_ITENS_REQUISICAO = 20

CONTAS_TELEFONIA = ["ENGIA", "BRASTEL", "ATTRON"]
OPERADORAS_TELEFONIA = ["Claro", "Vivo", "TIM", "Oi", "Algar", "Nextel", "Outra"]
STATUS_TELEFONIA = ["Ativo", "Inativo"]

# ── MELHORIA 4: GESTOR REMOVIDO ── Níveis: Leitor, Almoxarife, Master
NIVEIS_USUARIO = ["Leitor", "Almoxarife", "Master"]

# --- CSS GLOBAL ---
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Sora', sans-serif; }
.stButton > button {
    background: linear-gradient(135deg, #1a3a4a, #0d5c8a);
    color: white; border: none; border-radius: 8px; font-weight: 600; transition: opacity 0.2s;
}
.stButton > button:hover { opacity: 0.88; }
.req-detalhe-box { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px 18px; margin: 8px 0; font-size: 0.93rem; }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# 2. BANCO DE DADOS E CONEXÃO
# ══════════════════════════════════════════════════════════════════════════════
@contextmanager
def get_conn():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('CREATE TABLE IF NOT EXISTS estoque (id SERIAL PRIMARY KEY, "Codigo" TEXT, "Descricao" TEXT, "Quantidade" INTEGER, "CC" TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS acessos (sessao_id TEXT PRIMARY KEY, ultimo_clique TIMESTAMP)')
            c.execute('CREATE TABLE IF NOT EXISTS centros_custo (nome TEXT PRIMARY KEY)')
            c.execute('CREATE TABLE IF NOT EXISTS colaboradores (nome TEXT PRIMARY KEY)')
            c.execute('CREATE TABLE IF NOT EXISTS telefonia (id SERIAL PRIMARY KEY, "Numero" TEXT UNIQUE, "Conta" TEXT, "Operadora" TEXT, "Colaborador" TEXT, "CC" TEXT, "Status" TEXT DEFAULT \'Ativo\', "Gestor" TEXT)')
            # ── MELHORIA 4: constraint SEM Gestor ──
            c.execute('''
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    senha TEXT NOT NULL,
                    nome TEXT NOT NULL,
                    nivel TEXT CHECK (nivel IN (\'Leitor\', \'Almoxarife\', \'Master\')),
                    cc_permitido TEXT DEFAULT \'Todos\'
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS movimentacoes (
                    id SERIAL PRIMARY KEY, tipo TEXT CHECK (tipo IN ('RDM', 'CGM')),
                    cc_destino TEXT NOT NULL, solicitante_email TEXT NOT NULL,
                    retirante_nome TEXT NOT NULL, codigo_item TEXT, quantidade INTEGER,
                    status TEXT DEFAULT 'Pendente' CHECK (status IN ('Pendente', 'Aprovado', 'Rejeitado')),
                    data_solicitacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    data_aprovacao TIMESTAMP, aprovador_email TEXT
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS movimentacoes_itens (
                    id SERIAL PRIMARY KEY, movimentacao_id INTEGER REFERENCES movimentacoes(id) ON DELETE CASCADE,
                    codigo_item TEXT NOT NULL, quantidade INTEGER NOT NULL, descricao TEXT
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS notificacoes (
                    id SERIAL PRIMARY KEY, destinatario_email TEXT NOT NULL,
                    movimentacao_id INTEGER REFERENCES movimentacoes(id) ON DELETE CASCADE,
                    mensagem TEXT NOT NULL, lida BOOLEAN DEFAULT FALSE,
                    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            c.execute("SELECT count(*) as total FROM usuarios")
            if c.fetchone()['total'] == 0:
                c.execute("INSERT INTO usuarios (email, senha, nome, nivel, cc_permitido) VALUES (%s, %s, %s, %s, %s)",
                          ('master@brastelnet.com.br', SENHA_ZERAR_ESTOQUE, 'Administrador', 'Master', 'Todos'))

    # ── MELHORIA 4: migração — Gestor → Almoxarife + atualizar constraint ──
    try:
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute("UPDATE usuarios SET nivel = 'Almoxarife' WHERE nivel = 'Gestor'")
                c.execute("ALTER TABLE usuarios DROP CONSTRAINT IF EXISTS usuarios_nivel_check")
                c.execute("ALTER TABLE usuarios ADD CONSTRAINT usuarios_nivel_check CHECK (nivel IN ('Leitor', 'Almoxarife', 'Master'))")
    except Exception:
        pass

    try:
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute('ALTER TABLE movimentacoes ALTER COLUMN codigo_item DROP NOT NULL;')
                c.execute('ALTER TABLE movimentacoes ALTER COLUMN quantidade DROP NOT NULL;')
    except Exception:
        pass

init_db()

# ══════════════════════════════════════════════════════════════════════════════
# 3. HELPERS E SISTEMA DE PIN
# ══════════════════════════════════════════════════════════════════════════════
if 'usuario_logado' not in st.session_state:
    st.session_state.usuario_logado = None

def enviar_email_pin(destinatario, pin):
    """
    MELHORIA 3 — CORREÇÃO SMTP OUTLOOK:
    • Lê EMAIL_USER / EMAIL_PASS do bloco [email] do secrets.toml
    • Detecta office365 para domínios brastelnet/outlook/hotmail/live
    • ehlo() antes e depois de starttls() — obrigatório no Office 365
    • Timeout de 15 s para não travar a UI
    """
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = destinatario
        msg['Subject'] = "Seu Código de Autorização - Sistema Brastel"
        body = (
            f"Seu código PIN para autorizar uma ação Master no sistema de Almoxarifado é: {pin}\n\n"
            "Se não foi você, ignore este e-mail."
        )
        msg.attach(MIMEText(body, 'plain'))

        dominio_email = EMAIL_USER.lower()
        if any(x in dominio_email for x in ['@outlook', '@hotmail', '@live', '@office365', 'brastelnet']):
            smtp_server_default = 'smtp.office365.com'
        else:
            smtp_server_default = 'smtp.gmail.com'

        smtp_server = st.secrets.get("SMTP_SERVER", smtp_server_default)
        smtp_port   = int(st.secrets.get("SMTP_PORT", 587))

        server = smtplib.SMTP(smtp_server, smtp_port, timeout=15)
        server.ehlo()
        server.starttls()
        server.ehlo()  # segundo ehlo obrigatório após starttls no Office 365
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        server.quit()
        return True, ""
    except Exception as e:
        return False, str(e)

def aprovar_acao_master(chave, mensagem):
    if st.session_state.get(f"pin_validado_{chave}", False):
        st.session_state.pop(f"pin_validado_{chave}", None)
        st.session_state.pop(f"esperando_pin_{chave}", None)
        st.session_state.pop(f"pin_gerado_{chave}", None)
        return True

    st.warning(f"⚠️ Ação restrita: **{mensagem}**")

    if st.button(f"🔓 Solicitar Desbloqueio", key=f"btn_iniciar_{chave}"):
        st.session_state[f"esperando_pin_{chave}"] = True
        pin_novo = str(random.randint(100000, 999999))
        st.session_state[f"pin_gerado_{chave}"] = pin_novo
        sucesso, erro = enviar_email_pin(st.session_state.usuario_logado['email'], pin_novo)
        if sucesso:
            st.success("✉️ PIN enviado para o seu e-mail.")
        else:
            st.error(f"Erro no SMTP do e-mail: {erro}")
            st.info(f"FALLBACK DE SEGURANÇA: Seu PIN é {pin_novo}")

    if st.session_state.get(f"esperando_pin_{chave}", False):
        with st.container():
            st.info("Insira o código numérico de 6 dígitos enviado ao seu e-mail.")
            pin_digitado = st.text_input("PIN:", key=f"input_{chave}")
            c1, c2 = st.columns([1, 5])
            if c1.button("Validar PIN", key=f"btn_validar_{chave}", type="primary"):
                if pin_digitado.strip() == st.session_state.get(f"pin_gerado_{chave}"):
                    st.session_state[f"pin_validado_{chave}"] = True
                    st.rerun()
                else:
                    st.error("❌ PIN incorreto.")
            if c2.button("Cancelar", key=f"btn_cancelar_{chave}"):
                st.session_state.pop(f"esperando_pin_{chave}", None)
                st.rerun()
    return False

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
            row = cur.fetchone()
            if row:
                st.session_state.usuario_logado = dict(row)
                return True
    return False

def logout():
    st.session_state.usuario_logado = None
    st.rerun()

def ajustar_fuso_br(dt_obj):
    return dt_obj - timedelta(hours=3) if dt_obj else None

@st.cache_data
def logo_para_base64(path):
    for tentativa in [path, path.replace('.png', '.jpg'), path.replace('.png', '.jpeg')]:
        try:
            with open(tentativa, "rb") as f:
                return f"data:image/{'png' if tentativa.endswith('png') else 'jpeg'};base64,{base64.b64encode(f.read()).decode()}"
        except FileNotFoundError:
            continue
    return None

def criar_notificacao(dest_email, mov_id, msg):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO notificacoes (destinatario_email, movimentacao_id, mensagem) VALUES (%s, %s, %s)", (dest_email, mov_id, msg))
    except Exception:
        pass

def contar_notificacoes_nao_lidas(email: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as total FROM notificacoes WHERE destinatario_email=%s AND lida=FALSE", (email,))
            return cur.fetchone()['total']

def marcar_notificacoes_lidas(email: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE notificacoes SET lida=TRUE WHERE destinatario_email=%s", (email,))

# ══════════════════════════════════════════════════════════════════════════════
# 4. FUNÇÕES DE DADOS E CACHE
# ══════════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=120, max_entries=2)
def carregar_estoque():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('SELECT "Codigo", "Descricao", "Quantidade", "CC" FROM estoque')
            df = pd.DataFrame(c.fetchall(), columns=['Codigo', 'Descricao', 'Quantidade', 'CC'])
    if not df.empty:
        df['Quantidade'] = pd.to_numeric(df['Quantidade'], downcast='integer')
        df['CC'] = df['CC'].astype('category')
    return df

@st.cache_data(ttl=120, max_entries=2)
def carregar_telefonia():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('SELECT "Numero","Conta","Operadora","Colaborador","CC","Status","Gestor" FROM telefonia ORDER BY "Conta","Numero"')
            df_tel = pd.DataFrame(c.fetchall(), columns=['Numero', 'Conta', 'Operadora', 'Colaborador', 'CC', 'Status', 'Gestor'])
    if not df_tel.empty:
        for col in ['Conta', 'Operadora', 'CC', 'Status']:
            df_tel[col] = df_tel[col].astype('category')
    return df_tel

@st.cache_data
def carregar_ccs():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute("SELECT nome FROM centros_custo ORDER BY nome")
            return [r['nome'] for r in c.fetchall()] or ["Geral"]

@st.cache_data
def carregar_colaboradores():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute("SELECT nome FROM colaboradores ORDER BY nome")
            return [r['nome'] for r in c.fetchall()]

def buscar_descricao_por_codigo(cod):
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('SELECT DISTINCT "Descricao" FROM estoque WHERE "Codigo" = %s', (cod,))
            res = c.fetchone()
            return res['Descricao'] if res else None

def carregar_itens_movimentacao(mov_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT codigo_item, quantidade, descricao FROM movimentacoes_itens WHERE movimentacao_id=%s ORDER BY id", (mov_id,))
            itens = cur.fetchall()
            if not itens:
                cur.execute('SELECT codigo_item, quantidade FROM movimentacoes WHERE id=%s AND codigo_item IS NOT NULL', (mov_id,))
                row = cur.fetchone()
                if row and row['codigo_item']:
                    itens = [{'codigo_item': row['codigo_item'], 'quantidade': row['quantidade'], 'descricao': None}]
            return itens

@st.cache_data(show_spinner=False, max_entries=20, ttl=120)
def gerar_html_comprovante(req_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM movimentacoes WHERE id = %s", (req_id,))
            req = cur.fetchone()
            if not req:
                return None

    itens = carregar_itens_movimentacao(req_id)
    dt_sol = ajustar_fuso_br(req['data_solicitacao'])
    dt_apr = ajustar_fuso_br(req['data_aprovacao'])
    titulo = "REQUISIÇÃO DE MATERIAL (RDM)" if req['tipo'] == 'RDM' else "ENTREGA DE MATERIAL (CGM)"
    logo_b64 = logo_para_base64("logo1.png") or logo_para_base64("logo1.jpg")
    img_tag = f'<img src="{logo_b64}" style="max-height: 80px; margin-bottom: 10px;">' if logo_b64 else '<h2>BRASTEL</h2>'

    linhas_itens = ""
    for i, item in enumerate(itens, 1):
        desc = item.get('descricao') or item.get('Descricao') or ''
        cod  = item.get('codigo_item') or item.get('Codigo') or ''
        qtd  = item.get('quantidade') or item.get('Quantidade') or ''
        linhas_itens += f"<tr><td>{i}</td><td>{cod}</td><td>{desc}</td><td style='text-align:center'>{qtd}</td></tr>"

    return f"""
    <!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><title>Comprovante #{req['id']}</title>
    <style>body{{font-family:Arial,sans-serif;padding:40px;color:#333;max-width:850px;margin:auto;}}.header{{text-align:center;border-bottom:2px solid #333;padding-bottom:20px;margin-bottom:30px;}}.title{{font-size:22px;font-weight:bold;margin-bottom:5px;}}.subtitle{{font-size:18px;font-weight:bold;}}.info-box{{margin-bottom:20px;line-height:1.7;border:1px solid #ddd;padding:15px;border-radius:8px;}}table.itens{{width:100%;border-collapse:collapse;margin-top:8px;}}table.itens th{{background:#e9ecef;padding:7px 10px;text-align:left;font-size:0.9rem;}}table.itens td{{padding:6px 10px;border-bottom:1px solid #dee2e6;font-size:0.88rem;}}.signatures{{margin-top:80px;display:flex;justify-content:space-between;text-align:center;}}.sig-line{{width:45%;border-top:1px solid #333;padding-top:10px;font-weight:bold;}}@media print{{body{{padding:0;}}.no-print{{display:none;}}}}</style>
    </head><body onload="window.print()"><div class="no-print" style="text-align:center;margin-bottom:20px;"><button onclick="window.print()" style="padding:10px 20px;font-size:16px;cursor:pointer;">Imprimir Agora</button></div>
    <div class="header">{img_tag}<div class="title">CONTROLE DE ALMOXARIFADO</div><div class="subtitle">TERMO DE {titulo} - #{req['id']}</div></div>
    <div class="info-box"><strong>Data Solicitação:</strong> {dt_sol.strftime('%d/%m/%Y %H:%M')}<br><strong>Data Aprovação:</strong> {dt_apr.strftime('%d/%m/%Y %H:%M') if dt_apr else 'N/A'}<br><strong>Centro de Custo:</strong> {req['cc_destino']}<br><strong>Solicitante:</strong> {req['solicitante_email']}<br><strong>Retirante:</strong> {req['retirante_nome']}<br><strong>Aprovado por:</strong> {req['aprovador_email']}</div>
    <div class="info-box"><h3 style="margin-top:0;">ITENS:</h3><table class="itens"><tr><th>#</th><th>Código</th><th>Descrição</th><th style="text-align:center">Qtd</th></tr>{linhas_itens}</table></div>
    <div class="info-box" style="margin-top:30px;border:none;padding:0;"><h3 style="margin-bottom:5px;">Ato de Entrega/Retirada:</h3>Data física: ____/____/20___ &nbsp;&nbsp;&nbsp;&nbsp; Hora: ____:____</div>
    <div class="signatures"><div class="sig-line">Almoxarife</div><div class="sig-line">Assinatura de {req['retirante_nome']}</div></div></body></html>
    """

def gerar_template_xlsx():
    buf = io.BytesIO()
    pd.DataFrame({'Codigo': ['ABC'], 'Descricao': ['Parafuso'], 'Quantidade': [100], 'CC': ['01/0001']}).to_excel(buf, index=False, engine='openpyxl')
    return buf.getvalue()

def gerar_template_telefonia():
    buf = io.BytesIO()
    pd.DataFrame({'Numero': ['(11) 99999-0001'], 'Conta': ['BRASTEL'], 'Operadora': ['Claro'], 'Colaborador': ['João'], 'CC': ['01/0001'], 'Status': ['Ativo'], 'Gestor': ['Gestor']}).to_excel(buf, index=False, engine='openpyxl')
    return buf.getvalue()

def gerar_template_depara():
    buf = io.BytesIO()
    pd.DataFrame({'De': ['CC_Velho1'], 'Para': ['CC_Novo1']}).to_excel(buf, index=False, engine='openpyxl')
    return buf.getvalue()

def gerar_template_carga_massa():
    buf = io.BytesIO()
    pd.DataFrame({'Tipo': ['RDM', 'CGM'], 'CC': ['01/0001', '01/0002'], 'Colaborador': ['JOÃO DA SILVA', 'MARIA SOUZA'], 'Codigo_Item': ['ABC123', 'DEF456'], 'Quantidade': [2, 1]}).to_excel(buf, index=False, engine='openpyxl')
    return buf.getvalue()

# ══════════════════════════════════════════════════════════════════════════════
# MELHORIA 1: Zerar carga de colaboradores ao excluir um código do estoque
# ══════════════════════════════════════════════════════════════════════════════
def zerar_carga_por_codigo(codigo: str, aprovador_email: str) -> int:
    """
    Cria CGMs automáticas de devolução para todos os colaboradores que
    têm saldo em mãos do código informado. Retorna nº de devoluções criadas.
    """
    devolvidos = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    m.retirante_nome,
                    m.cc_destino,
                    mi.codigo_item,
                    MAX(mi.descricao) AS descricao,
                    SUM(CASE WHEN m.tipo = 'RDM' THEN mi.quantidade ELSE -mi.quantidade END) AS saldo
                FROM movimentacoes_itens mi
                JOIN movimentacoes m ON m.id = mi.movimentacao_id
                WHERE m.status = 'Aprovado' AND mi.codigo_item = %s
                GROUP BY m.retirante_nome, m.cc_destino, mi.codigo_item
                HAVING SUM(CASE WHEN m.tipo = 'RDM' THEN mi.quantidade ELSE -mi.quantidade END) > 0
            """, (codigo,))
            pendentes = cur.fetchall()

            for p in pendentes:
                cur.execute("""
                    INSERT INTO movimentacoes
                        (tipo, cc_destino, solicitante_email, retirante_nome, status, data_aprovacao, aprovador_email)
                    VALUES ('CGM', %s, %s, %s, 'Aprovado', NOW(), %s) RETURNING id
                """, (p['cc_destino'], aprovador_email, p['retirante_nome'], aprovador_email))
                cid = cur.fetchone()['id']
                cur.execute(
                    "INSERT INTO movimentacoes_itens (movimentacao_id, codigo_item, quantidade, descricao) VALUES (%s, %s, %s, %s)",
                    (cid, p['codigo_item'], p['saldo'], p['descricao'])
                )
                devolvidos += 1
    return devolvidos

# ══════════════════════════════════════════════════════════════════════════════
# 5. CONTROLE DE SESSÃO
# ══════════════════════════════════════════════════════════════════════════════
if 'sessao_id' not in st.session_state:
    st.session_state.sessao_id = str(uuid.uuid4())

with get_conn() as conn:
    with conn.cursor() as c:
        c.execute("DELETE FROM acessos WHERE ultimo_clique < %s", (datetime.now() - timedelta(minutes=TEMPO_INATIVIDADE),))
        c.execute("INSERT INTO acessos (sessao_id, ultimo_clique) VALUES (%s, %s) ON CONFLICT (sessao_id) DO UPDATE SET ultimo_clique = EXCLUDED.ultimo_clique", (st.session_state.sessao_id, datetime.now()))
        c.execute("SELECT COUNT(*) as total FROM acessos")
        total_ativos = c.fetchone()['total']

if total_ativos > LIMITE_PESSOAS:
    st.error(f"⚠️ Sistema lotado ({total_ativos}/{LIMITE_PESSOAS} usuários). Tente novamente em 1 minuto.")
    st.stop()

df = carregar_estoque()
df_tel = carregar_telefonia()
lista_cc = carregar_ccs()
lista_colabs = carregar_colaboradores()

# ══════════════════════════════════════════════════════════════════════════════
# 6. MENU LATERAL E TELAS PÚBLICAS
# ══════════════════════════════════════════════════════════════════════════════
st.sidebar.title("Navegação")
menu = st.sidebar.radio("Ir para:", ["📊 Consulta", "📱 Telefonia", "🔒 Sistema Interno"])
st.sidebar.divider()

if st.session_state.usuario_logado:
    user = st.session_state.usuario_logado
    qtd_notif = contar_notificacoes_nao_lidas(user['email'])
    badge = f" 🔴 {qtd_notif}" if qtd_notif > 0 else ""
    st.sidebar.success(f"👤 Olá, {user['nome']}{badge}\n\nNível: {user['nivel']}")
    if st.sidebar.button("Sair / Logout"):
        logout()
st.sidebar.markdown(f"🟢 **{total_ativos}/{LIMITE_PESSOAS}** pessoas online")

if menu == "📊 Consulta":
    st.title("📦 Inventário Brastel")
    df_ativos = df[df['Quantidade'] > 0]
    col1, col2, col3 = st.columns(3)
    col1.metric("Total de Peças em Estoque", f"{df_ativos['Quantidade'].sum():.0f}")
    col2.metric("Itens Únicos Diferentes", df_ativos['Codigo'].nunique())
    col3.metric("Centros de Custo Ativos", df_ativos['CC'].nunique())
    st.divider()
    c_b, c_f = st.columns([2, 1])
    busca = c_b.text_input("🔍 Pesquisar Código ou Descrição:")
    cc_filtro = c_f.selectbox("🏢 Filtrar por Centro de Custo:", ["Todos"] + lista_cc)
    df_filt = df_ativos.copy()
    if cc_filtro != "Todos": df_filt = df_filt[df_filt['CC'] == cc_filtro]
    if busca: df_filt = df_filt[df_filt['Codigo'].astype(str).str.contains(busca, case=False) | df_filt['Descricao'].str.contains(busca, case=False, na=False)]
    if not df_filt.empty:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as writer: df_filt.to_excel(writer, index=False)
        st.download_button("📥 Baixar Excel", data=buf.getvalue(), file_name="Consulta.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.dataframe(df_filt, use_container_width=True, hide_index=True)

elif menu == "📱 Telefonia":
    st.title("📱 Telefonia Brastel")
    col1, col2, col3 = st.columns(3)
    col1.metric("Total de Linhas Registradas", len(df_tel))
    col2.metric("Linhas Ativas", len(df_tel[df_tel['Status'] == 'Ativo']) if not df_tel.empty else 0)
    col3.metric("Centros de Custo Associados", df_tel['CC'].nunique() if not df_tel.empty else 0)
    st.divider()
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    busca_tel = c1.text_input("🔍 Pesquisar Número/Colaborador:")
    conta_filt = c2.selectbox("🏢 Conta:", ["Todas"] + CONTAS_TELEFONIA)
    status_filt = c3.selectbox("✅ Status:", ["Todos"] + STATUS_TELEFONIA)
    cc_filt_tel = c4.selectbox("📂 Centro de Custo:", ["Todos"] + lista_cc)
    df_tel_filt = df_tel.copy()
    if conta_filt != "Todas": df_tel_filt = df_tel_filt[df_tel_filt['Conta'] == conta_filt]
    if status_filt != "Todos": df_tel_filt = df_tel_filt[df_tel_filt['Status'] == status_filt]
    if cc_filt_tel != "Todos": df_tel_filt = df_tel_filt[df_tel_filt['CC'] == cc_filt_tel]
    if busca_tel: df_tel_filt = df_tel_filt[df_tel_filt['Numero'].astype(str).str.contains(busca_tel, case=False) | df_tel_filt['Colaborador'].astype(str).str.contains(busca_tel, case=False, na=False)]
    if not df_tel_filt.empty:
        buf2 = io.BytesIO()
        with pd.ExcelWriter(buf2, engine='openpyxl') as writer: df_tel_filt.to_excel(writer, index=False)
        st.download_button("📥 Baixar Excel", data=buf2.getvalue(), file_name="Telefonia.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.dataframe(df_tel_filt, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# 7. SISTEMA INTERNO E MÓDULOS DE GESTÃO
# ══════════════════════════════════════════════════════════════════════════════
else:
    if not st.session_state.usuario_logado:
        st.title("🔒 Acesso Restrito")
        with st.form("login_form"):
            email_login = st.text_input("E-mail Corporativo:")
            senha_login = st.text_input("Senha:", type="password")
            if st.form_submit_button("Entrar"):
                if realizar_login(email_login, senha_login): st.rerun()
                else: st.error("Credenciais inválidas.")
    else:
        user = st.session_state.usuario_logado
        st.title("Sistema Interno — Brastel")
        cc_opcoes = lista_cc if user['cc_permitido'] == 'Todos' else [c.strip() for c in user['cc_permitido'].split('|')]

        qtd_notif = contar_notificacoes_nao_lidas(user['email'])
        if qtd_notif > 0:
            with st.expander(f"🔔 Você tem **{qtd_notif}** notificação(ões) não lida(s) — clique para ver", expanded=False):
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT * FROM notificacoes WHERE destinatario_email=%s ORDER BY criado_em DESC LIMIT 20", (user['email'],))
                        notifs = cur.fetchall()
                for n in notifs:
                    icone = "🟢" if n['lida'] else "🔵"
                    st.markdown(f"{icone} **{ajustar_fuso_br(n['criado_em']).strftime('%d/%m %H:%M')}** — {n['mensagem']}")
                if st.button("✅ Marcar todas como lidas"):
                    marcar_notificacoes_lidas(user['email']); st.rerun()

        # ── MELHORIA 4: módulos sem Gestor ──
        modulos_disp = ["🛒 Requisições (RDM/CGM)", "👁️ Carga por Colaborador", "👤 Meu Perfil"]
        if user['nivel'] in ['Almoxarife', 'Master']:
            modulos_disp.extend(["📦 Gestão de Estoque", "📱 Telefonia", "📋 Carga em Massa", "📜 Relatórios e Logs"])
        if user['nivel'] == 'Master':
            modulos_disp.append("⚙️ Administração")

        modulo_ativo = st.radio("Módulo:", modulos_disp, horizontal=True)
        st.divider()

        # ────────────────────────────────────────────
        if modulo_ativo == "👤 Meu Perfil":
            st.subheader("Configurações da Conta")
            st.write(f"**Nome:** {user['nome']}\n\n**E-mail:** {user['email']}\n\n**Nível:** {user['nivel']}")
            with st.form("form_senha"):
                st.write("🔒 **Alterar Senha**")
                senha_atual, nova_senha, conf_senha = st.text_input("Atual", type="password"), st.text_input("Nova", type="password"), st.text_input("Confirmar", type="password")
                if st.form_submit_button("Atualizar"):
                    if not senha_atual or not nova_senha or not conf_senha: st.error("Preencha todos os campos.")
                    elif senha_atual != user['senha']: st.error("Senha atual incorreta.")
                    elif nova_senha != conf_senha: st.error("Senhas não coincidem.")
                    elif len(nova_senha) < 4: st.error("Mínimo 4 caracteres.")
                    else:
                        with get_conn() as conn:
                            with conn.cursor() as cur: cur.execute("UPDATE usuarios SET senha=%s WHERE id=%s", (nova_senha, user['id']))
                        st.session_state.usuario_logado['senha'] = nova_senha; st.success("Atualizada!")

        elif modulo_ativo == "👁️ Carga por Colaborador":
            st.subheader("🎒 Rastreabilidade de Materiais em Posse")
            colab_alvo = st.selectbox("Selecione o Colaborador:", [""] + lista_colabs)
            if colab_alvo:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT mi.codigo_item AS "Código", MAX(mi.descricao) AS "Descrição",
                                SUM(CASE WHEN m.tipo = 'RDM' THEN mi.quantidade ELSE 0 END) -
                                SUM(CASE WHEN m.tipo = 'CGM' THEN mi.quantidade ELSE 0 END) AS "Saldo em Mãos"
                            FROM movimentacoes_itens mi JOIN movimentacoes m ON m.id = mi.movimentacao_id
                            WHERE m.status = 'Aprovado' AND m.retirante_nome = %s
                            GROUP BY mi.codigo_item
                            HAVING (SUM(CASE WHEN m.tipo = 'RDM' THEN mi.quantidade ELSE 0 END) -
                                    SUM(CASE WHEN m.tipo = 'CGM' THEN mi.quantidade ELSE 0 END)) > 0
                        """, (colab_alvo,))
                        carga = cur.fetchall()
                        cur.execute("""
                            SELECT m.codigo_item AS "Código", NULL AS "Descrição",
                                SUM(CASE WHEN m.tipo = 'RDM' THEN m.quantidade ELSE 0 END) -
                                SUM(CASE WHEN m.tipo = 'CGM' THEN m.quantidade ELSE 0 END) AS "Saldo em Mãos"
                            FROM movimentacoes m
                            WHERE m.status = 'Aprovado' AND m.retirante_nome = %s
                              AND m.codigo_item IS NOT NULL
                              AND m.id NOT IN (SELECT DISTINCT movimentacao_id FROM movimentacoes_itens)
                            GROUP BY m.codigo_item
                            HAVING (SUM(CASE WHEN m.tipo = 'RDM' THEN m.quantidade ELSE 0 END) -
                                    SUM(CASE WHEN m.tipo = 'CGM' THEN m.quantidade ELSE 0 END)) > 0
                        """, (colab_alvo,))
                        carga_total = list(carga) + list(cur.fetchall())
                if not carga_total: st.success("✅ Sem materiais pendentes.")
                else: st.warning(f"⚠️ Materiais alocados para {colab_alvo}:"); st.dataframe(pd.DataFrame(carga_total), use_container_width=True, hide_index=True)
                st.divider(); st.markdown("#### Histórico")
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute('''
                            SELECT m.id, m.tipo AS "Operação", mi.codigo_item AS "Código", mi.quantidade AS "Qtd", m.data_aprovacao AS "Data"
                            FROM movimentacoes m JOIN movimentacoes_itens mi ON mi.movimentacao_id = m.id WHERE m.status = 'Aprovado' AND m.retirante_nome = %s
                            UNION ALL
                            SELECT id, tipo, codigo_item, quantidade, data_aprovacao FROM movimentacoes
                            WHERE status = 'Aprovado' AND retirante_nome = %s AND codigo_item IS NOT NULL
                              AND id NOT IN (SELECT DISTINCT movimentacao_id FROM movimentacoes_itens)
                            ORDER BY "Data" DESC
                        ''', (colab_alvo, colab_alvo))
                        hist = cur.fetchall()
                if hist:
                    df_h = pd.DataFrame(hist)
                    df_h['Data'] = pd.to_datetime(df_h['Data']).dt.tz_localize('UTC').dt.tz_convert('America/Sao_Paulo').dt.strftime('%d/%m/%Y %H:%M')
                    st.dataframe(df_h, hide_index=True)

        elif modulo_ativo == "🛒 Requisições (RDM/CGM)":
            abas_req = ["Nova Solicitação"]
            if user['nivel'] in ['Almoxarife', 'Master']: abas_req.append("✅ Aprovações Pendentes")
            tabs_req = st.tabs(abas_req)

            with tabs_req[0]:
                # ── MELHORIA 2: avisos se não há CC ou colaborador cadastrado ──
                if not lista_colabs:
                    st.error("⛔ Nenhum Colaborador cadastrado. Solicite o cadastro ao administrador antes de criar uma solicitação.")
                elif not lista_cc:
                    st.error("⛔ Nenhum Centro de Custo cadastrado. Solicite o cadastro ao administrador antes de criar uma solicitação.")
                else:
                    with st.form("form_nova_solicitacao", clear_on_submit=True):
                        col_t1, col_t2 = st.columns(2)
                        tipo_req = col_t1.radio("Tipo:", ["RDM (Retirar Material)", "CGM (Devolver Material)"], horizontal=True)
                        tipo_db = "RDM" if "RDM" in tipo_req else "CGM"
                        # ── MELHORIA 2: selectbox limitado a CCs cadastrados ──
                        cc_req = col_t2.selectbox("Centro de Custo:", cc_opcoes)
                        # ── MELHORIA 2: selectbox limitado a colaboradores cadastrados ──
                        retirante = st.selectbox("Colaborador Responsável (Que irá receber/devolver):", lista_colabs)

                        st.markdown(f"#### 📦 Itens da Solicitação *(máx. {MAX_ITENS_REQUISICAO})*")
                        if 'grid_itens' not in st.session_state:
                            st.session_state.grid_itens = pd.DataFrame([{"Código": "", "Quantidade": 1} for _ in range(5)])

                        edited_df = st.data_editor(
                            st.session_state.grid_itens, num_rows="dynamic", use_container_width=True,
                            column_config={
                                "Código": st.column_config.TextColumn("Código do Item", required=True),
                                "Quantidade": st.column_config.NumberColumn("Quantidade", min_value=1, step=1, default=1)
                            }, key="editor_itens_req"
                        )

                        if st.form_submit_button("🚀 Processar e Enviar Solicitação", type="primary", use_container_width=True):
                            # ── MELHORIA 2: validação dupla no submit ──
                            erros_trava = []
                            if cc_req not in lista_cc:
                                erros_trava.append(f"❌ Centro de Custo **{cc_req}** não está cadastrado.")
                            if retirante not in lista_colabs:
                                erros_trava.append(f"❌ Colaborador **{retirante}** não está cadastrado.")

                            if erros_trava:
                                for e in erros_trava: st.error(e)
                            else:
                                df_v = edited_df.copy()
                                df_v['Código'] = df_v['Código'].astype(str).str.strip()
                                df_v = df_v[(df_v['Código'] != "") & (df_v['Código'] != "nan")]

                                if df_v.empty: st.error("⛔ Adicione itens.")
                                elif len(df_v) > MAX_ITENS_REQUISICAO: st.error("⛔ Limite excedido.")
                                else:
                                    erros, itens_proc = [], []
                                    df_g = df_v.groupby("Código", as_index=False).sum()
                                    for _, row in df_g.iterrows():
                                        cod, qtd = str(row["Código"]), int(row["Quantidade"])
                                        desc = buscar_descricao_por_codigo(cod)
                                        if not desc: erros.append(f"❌ Código **{cod}** não existe.")
                                        elif tipo_db == "RDM":
                                            saldo = df[(df['Codigo'] == cod) & (df['CC'] == cc_req)]['Quantidade'].sum() if not df[(df['Codigo'] == cod) & (df['CC'] == cc_req)].empty else 0
                                            if saldo < qtd: erros.append(f"❌ **{cod}**: Saldo insuf. ({saldo} no CC {cc_req}).")
                                        if not erros: itens_proc.append({'codigo': cod, 'quantidade': qtd, 'descricao': desc})

                                    if erros:
                                        st.error("⛔ Corrija:")
                                        for e in erros: st.write(e)
                                    else:
                                        with get_conn() as conn:
                                            with conn.cursor() as cur:
                                                cur.execute('INSERT INTO movimentacoes (tipo, cc_destino, solicitante_email, retirante_nome) VALUES (%s, %s, %s, %s) RETURNING id', (tipo_db, cc_req, user['email'], retirante))
                                                novo_id = cur.fetchone()['id']
                                                for item in itens_proc:
                                                    cur.execute('INSERT INTO movimentacoes_itens (movimentacao_id, codigo_item, quantidade, descricao) VALUES (%s, %s, %s, %s)', (novo_id, item['codigo'], item['quantidade'], item['descricao']))
                                        st.success(f"✅ Solicitação #{novo_id} enviada!")
                                        st.session_state.grid_itens = pd.DataFrame([{"Código": "", "Quantidade": 1} for _ in range(5)])
                                        st.rerun()

            if len(abas_req) > 1:
                with tabs_req[1]:
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT * FROM movimentacoes WHERE status = 'Pendente' ORDER BY data_solicitacao ASC")
                            pendentes = cur.fetchall()
                    if not pendentes: st.info("Nenhuma pendente.")
                    else:
                        for req in pendentes:
                            itens_req = carregar_itens_movimentacao(req['id'])
                            with st.expander(f"[{req['tipo']}] #{req['id']} | {len(itens_req)} item(s) | Resp: {req['retirante_nome']} | CC: {req['cc_destino']}", expanded=False):
                                dt_sol = ajustar_fuso_br(req['data_solicitacao'])
                                st.markdown(f"<div class='req-detalhe-box'><b>Solicitante:</b> {req['solicitante_email']} | <b>Data:</b> {dt_sol.strftime('%d/%m/%Y %H:%M') if dt_sol else 'N/A'}</div>", unsafe_allow_html=True)
                                if itens_req:
                                    df_i = pd.DataFrame(itens_req).rename(columns={'codigo_item': 'Código', 'quantidade': 'Qtd', 'descricao': 'Descrição'})
                                    if req['tipo'] == 'RDM':
                                        saldos = []
                                        for _, ri in df_i.iterrows():
                                            ds = df[(df['Codigo'] == ri['Código']) & (df['CC'] == req['cc_destino'])]
                                            sa = ds['Quantidade'].sum() if not ds.empty else 0
                                            saldos.append(f"{sa} ({'✅' if sa >= ri['Qtd'] else '⚠️'})")
                                        df_i['Estoque Atual'] = saldos
                                    st.dataframe(df_i, use_container_width=True, hide_index=True)
                                c_btn1, c_btn2, _ = st.columns([1, 1, 3])
                                if c_btn1.button("✅ Aprovar", key=f"apr_{req['id']}"):
                                    try:
                                        with get_conn() as conn:
                                            with conn.cursor() as cur:
                                                for item in itens_req:
                                                    cd = item.get('codigo_item') or item.get('Codigo')
                                                    qt = item.get('quantidade') or item.get('Quantidade')
                                                    if req['tipo'] == 'RDM':
                                                        cur.execute('SELECT "Quantidade" FROM estoque WHERE "Codigo"=%s AND "CC"=%s FOR UPDATE', (cd, req['cc_destino']))
                                                        saldo_db = cur.fetchone()
                                                        if not saldo_db or saldo_db['Quantidade'] < qt: raise Exception(f"Sem estoque para {cd}")
                                                        cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" - %s WHERE "Codigo"=%s AND "CC"=%s', (qt, cd, req['cc_destino']))
                                                    else:
                                                        cur.execute('SELECT id FROM estoque WHERE "Codigo"=%s AND "CC"=%s', (cd, req['cc_destino']))
                                                        if cur.fetchone(): cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" + %s WHERE "Codigo"=%s AND "CC"=%s', (qt, cd, req['cc_destino']))
                                                        else:
                                                            desc_ins = (buscar_descricao_por_codigo(cd) or '') if not item.get('descricao') else item.get('descricao')
                                                            cur.execute('INSERT INTO estoque ("Codigo","Descricao","Quantidade","CC") VALUES (%s,%s,%s,%s)', (cd, desc_ins, qt, req['cc_destino']))
                                                cur.execute("UPDATE movimentacoes SET status='Aprovado', data_aprovacao=NOW(), aprovador_email=%s WHERE id=%s", (user['email'], req['id']))
                                        criar_notificacao(req['solicitante_email'], req['id'], f"Aprovada por {user['nome']}.")
                                        st.success("✅ Aprovado!"); st.cache_data.clear(); st.rerun()
                                    except Exception as e: st.error(f"Erro: {e}")
                                if c_btn2.button("❌ Rejeitar", key=f"rej_{req['id']}"):
                                    with get_conn() as conn:
                                        with conn.cursor() as cur: cur.execute("UPDATE movimentacoes SET status='Rejeitado', aprovador_email=%s WHERE id=%s", (user['email'], req['id']))
                                    criar_notificacao(req['solicitante_email'], req['id'], f"Rejeitada por {user['nome']}.")
                                    st.rerun()
                    st.divider(); st.subheader("🖨️ Comprovantes")
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT id, tipo, data_aprovacao FROM movimentacoes WHERE status = 'Aprovado' ORDER BY data_aprovacao DESC LIMIT 10")
                            aprovados = cur.fetchall()
                    for ap in aprovados:
                        c_txt, c_dl = st.columns([3, 1])
                        c_txt.write(f"#{ap['id']} - {ap['tipo']} (Aprov: {ajustar_fuso_br(ap['data_aprovacao']).strftime('%d/%m %H:%M')})")
                        html_str = gerar_html_comprovante(ap['id'])
                        if html_str: c_dl.download_button("🖨️ Baixar", data=html_str, file_name=f"Comp_{ap['tipo']}_{ap['id']}.html", mime="text/html", key=f"dl_{ap['id']}")

        elif modulo_ativo == "📜 Relatórios e Logs":
            st.subheader("Auditoria de Movimentações")
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT m.id as "ID", m.tipo as "Tipo", m.status as "Status", m.solicitante_email as "Solicitante",
                               m.aprovador_email as "Aprovador", m.retirante_nome as "Colaborador", m.cc_destino as "CC",
                               mi.codigo_item as "Código", mi.descricao as "Descrição", mi.quantidade as "Qtd",
                               m.data_solicitacao as "Data Pedido", m.data_aprovacao as "Data Aprovação"
                        FROM movimentacoes m LEFT JOIN movimentacoes_itens mi ON m.id = mi.movimentacao_id ORDER BY m.id DESC
                    """)
                    logs = cur.fetchall()
            if logs:
                df_logs = pd.DataFrame(logs)
                df_logs['Data Pedido'] = pd.to_datetime(df_logs['Data Pedido']).dt.tz_localize('UTC').dt.tz_convert('America/Sao_Paulo').dt.strftime('%d/%m/%Y %H:%M')
                df_logs['Data Aprovação'] = pd.to_datetime(df_logs['Data Aprovação']).dt.tz_localize('UTC').dt.tz_convert('America/Sao_Paulo').dt.strftime('%d/%m/%Y %H:%M')
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine='openpyxl') as writer: df_logs.to_excel(writer, index=False)
                st.download_button("📥 Baixar Relatório (Excel)", data=buf.getvalue(), file_name="Logs.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                st.dataframe(df_logs, use_container_width=True, hide_index=True)

        elif modulo_ativo == "📋 Carga em Massa":
            st.subheader("📤 Importar RDMs/CGMs")
            st.download_button("⬇️ Template Carga", gerar_template_carga_massa(), "carga.xlsx")
            arq_massa = st.file_uploader("Arquivo (.xlsx):", type=["xlsx"])
            if arq_massa:
                df_massa = pd.read_excel(arq_massa, engine='openpyxl').fillna('')
                colunas_esperadas = {'Tipo', 'CC', 'Colaborador', 'Codigo_Item', 'Quantidade'}
                if colunas_esperadas - set(df_massa.columns): st.error(f"Colunas obrigatórias: {', '.join(colunas_esperadas)}")
                else:
                    df_massa['Tipo'] = df_massa['Tipo'].astype(str).str.upper().str.strip()
                    df_massa['Quantidade'] = pd.to_numeric(df_massa['Quantidade'], errors='coerce')
                    df_massa = df_massa.dropna(subset=['Quantidade'])
                    df_massa['Quantidade'] = df_massa['Quantidade'].astype(int)
                    erros_massa = []
                    for idx, row in df_massa.iterrows():
                        if row['Tipo'] not in ('RDM', 'CGM'): erros_massa.append(f"L{idx+2}: Tipo '{row['Tipo']}' inválido.")
                        # ── MELHORIA 2: validar CC e colaborador na carga em massa ──
                        if row['CC'] not in lista_cc: erros_massa.append(f"L{idx+2}: CC '{row['CC']}' não está cadastrado.")
                        if row['Colaborador'] not in lista_colabs: erros_massa.append(f"L{idx+2}: Colab '{row['Colaborador']}' não está cadastrado.")
                    if erros_massa:
                        st.error("Corrija antes de importar:")
                        for e in erros_massa[:10]: st.write(e)
                    else:
                        st.success(f"Pré-validação OK ({len(df_massa)} linhas).")
                        if st.button("🚀 Importar", type="primary"):
                            grupos = df_massa.groupby(['Tipo', 'CC', 'Colaborador'])
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    for (tipo_g, cc_g, colab_g), grupo in grupos:
                                        cur.execute('''INSERT INTO movimentacoes (tipo, cc_destino, solicitante_email, retirante_nome, status, data_aprovacao, aprovador_email) VALUES (%s, %s, %s, %s, 'Aprovado', NOW(), %s) RETURNING id''', (tipo_g, cc_g, user['email'], colab_g, user['email']))
                                        mov_id = cur.fetchone()['id']
                                        for _, ir in grupo.iterrows():
                                            di = buscar_descricao_por_codigo(ir['Codigo_Item']) or ''
                                            cur.execute('INSERT INTO movimentacoes_itens (movimentacao_id, codigo_item, quantidade, descricao) VALUES (%s, %s, %s, %s)', (mov_id, ir['Codigo_Item'], ir['Quantidade'], di))
                                            if tipo_g == 'RDM': cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" - %s WHERE "Codigo"=%s AND "CC"=%s', (ir['Quantidade'], ir['Codigo_Item'], cc_g))
                                            else:
                                                cur.execute('SELECT id FROM estoque WHERE "Codigo"=%s AND "CC"=%s', (ir['Codigo_Item'], cc_g))
                                                if cur.fetchone(): cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" + %s WHERE "Codigo"=%s AND "CC"=%s', (ir['Quantidade'], ir['Codigo_Item'], cc_g))
                                                else: cur.execute('INSERT INTO estoque ("Codigo","Descricao","Quantidade","CC") VALUES (%s,%s,%s,%s)', (ir['Codigo_Item'], di, ir['Quantidade'], cc_g))
                            st.success("✅ Importação concluída!"); st.cache_data.clear(); st.rerun()

        elif modulo_ativo == "📦 Gestão de Estoque":
            tabs_est = st.tabs(["📝 Manual", "📤 Carga Excel"])
            with tabs_est[0]:
                with st.form("registro", clear_on_submit=True):
                    c1, c2 = st.columns(2)
                    cod, desc_input = c1.text_input("Código:"), c2.text_input("Descrição:")
                    c3, c4, c5 = st.columns([2, 2, 1])
                    cc_sel = c3.selectbox("CC:", lista_cc)
                    # ── MELHORIA 1: opção "Excluir Código" adicionada ──
                    op = c4.selectbox("Operação:", ["Entrada", "Saída", "Excluir Código"])
                    qtd = c5.number_input("Qtd:", min_value=1, step=1)
                    if st.form_submit_button("✅ Confirmar"):
                        if not cod:
                            st.error("Informe o Código.")
                        elif op == "Excluir Código":
                            # ── MELHORIA 1: cria CGMs automáticas antes de excluir ──
                            n_dev = zerar_carga_por_codigo(cod, user['email'])
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    cur.execute('DELETE FROM estoque WHERE "Codigo"=%s', (cod,))
                            msg_dev = f" {n_dev} devolução(ões) automática(s) criada(s) para colaboradores com carga pendente." if n_dev > 0 else ""
                            st.success(f"✅ Código **{cod}** excluído.{msg_dev}")
                            st.cache_data.clear()
                        else:
                            de = buscar_descricao_por_codigo(cod)
                            if not de and not desc_input: st.error("Descrição obrigatória p/ item novo.")
                            else:
                                with get_conn() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute('SELECT "Quantidade" FROM estoque WHERE "Codigo"=%s AND "CC"=%s', (cod, cc_sel))
                                        res = cur.fetchone()
                                        if res:
                                            if op == "Saída":
                                                if res['Quantidade'] < qtd: st.error("FALTA DE ESTOQUE!")
                                                else: cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" - %s WHERE "Codigo"=%s AND "CC"=%s', (qtd, cod, cc_sel)); st.success("Saída!")
                                            else: cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" + %s WHERE "Codigo"=%s AND "CC"=%s', (qtd, cod, cc_sel)); st.success("Entrada!")
                                        else:
                                            if op == "Saída": st.error("ITEM NÃO ENCONTRADO.")
                                            else: cur.execute('INSERT INTO estoque ("Codigo", "Descricao", "Quantidade", "CC") VALUES (%s, %s, %s, %s)', (cod, de or desc_input, qtd, cc_sel)); st.success("Cadastrado!")
                                st.cache_data.clear()
            with tabs_est[1]:
                st.download_button("⬇️ Template", gerar_template_xlsx(), "template.xlsx")
                arquivo = st.file_uploader("Arquivo (.xlsx):", type=["xlsx"])
                if arquivo and st.button("🚀 Processar Importação"):
                    df_u = pd.read_excel(arquivo, engine='openpyxl')
                    if {'Codigo', 'Descricao', 'Quantidade', 'CC'} - set(df_u.columns): st.error("Colunas inválidas.")
                    else:
                        df_u['Codigo'], df_u['CC'] = df_u['Codigo'].astype(str).str.strip(), df_u['CC'].astype(str).str.strip()
                        df_u['Quantidade'] = pd.to_numeric(df_u['Quantidade'], errors='coerce')
                        df_u = df_u.dropna(subset=['Quantidade'])
                        inv_ccs = set(df_u['CC'].unique()) - set(lista_cc)
                        if inv_ccs: st.error(f"CCs não encontrados: {', '.join(inv_ccs)}")
                        else:
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    cur.execute('SELECT "Codigo", "CC" FROM estoque')
                                    db_set = set((r['Codigo'], r['CC']) for r in cur.fetchall())
                                    ins, upd = [], []
                                    for _, row in df_u.iterrows():
                                        if (row['Codigo'], row['CC']) in db_set: upd.append((row['Quantidade'], row['Codigo'], row['CC']))
                                        else: ins.append((row['Codigo'], row['Descricao'], row['Quantidade'], row['CC'])); db_set.add((row['Codigo'], row['CC']))
                                    if ins: cur.executemany('INSERT INTO estoque ("Codigo","Descricao","Quantidade","CC") VALUES (%s,%s,%s,%s)', ins)
                                    if upd: cur.executemany('UPDATE estoque SET "Quantidade" = "Quantidade" + %s WHERE "Codigo"=%s AND "CC"=%s', upd)
                            st.success("Importação concluída!"); st.cache_data.clear(); st.rerun()

        elif modulo_ativo == "📱 Telefonia":
            tabs_tel = st.tabs(["📱 Individual", "📤 Em Massa"])
            with tabs_tel[0]:
                acao_tel = st.radio("Operação:", ["➕ Nova", "✏️ Editar", "🔄 Status"], horizontal=True)
                if acao_tel == "➕ Nova":
                    with st.form("nt"):
                        tc1, tc2 = st.columns(2); n_raw = tc1.text_input("Número:"); cta = tc2.selectbox("Conta:", CONTAS_TELEFONIA)
                        tc3, tc4 = st.columns(2); opr = tc3.selectbox("Operadora:", OPERADORAS_TELEFONIA); clb = tc4.text_input("Colab:")
                        tc5, tc6 = st.columns(2); cc_n = tc5.selectbox("CC:", lista_cc); gst = tc6.text_input("Gestor:")
                        if st.form_submit_button("✅ Cadastrar"):
                            nf = formatar_numero(n_raw)
                            if not nf: st.error("Número inválido.")
                            else:
                                with get_conn() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute('SELECT id FROM telefonia WHERE "Numero"=%s', (nf,))
                                        if cur.fetchone(): st.error("Número já existe.")
                                        else: cur.execute('INSERT INTO telefonia ("Numero","Conta","Operadora","Colaborador","CC","Status","Gestor") VALUES (%s,%s,%s,%s,%s,%s,%s)', (nf, cta, opr, clb.strip(), cc_n, 'Ativo', gst.strip())); st.success("Cadastrado!")
                                st.cache_data.clear()
                elif acao_tel == "✏️ Editar":
                    ne = st.text_input("Número:")
                    if ne:
                        nf = formatar_numero(ne)
                        if not nf: st.error("Inválido.")
                        else:
                            with get_conn() as conn:
                                with conn.cursor() as cur: cur.execute('SELECT * FROM telefonia WHERE "Numero"=%s', (nf,)); l = cur.fetchone()
                            if not l: st.warning("Não encontrado.")
                            else:
                                with st.form("edl"):
                                    c1, c2 = st.columns(2)
                                    cta = c1.selectbox("Conta:", CONTAS_TELEFONIA, index=CONTAS_TELEFONIA.index(l['Conta']) if l['Conta'] in CONTAS_TELEFONIA else 0)
                                    opr = c2.selectbox("Operadora:", OPERADORAS_TELEFONIA, index=OPERADORAS_TELEFONIA.index(l['Operadora']) if l['Operadora'] in OPERADORAS_TELEFONIA else 0)
                                    c3, c4 = st.columns(2); clb = c3.text_input("Colab:", l['Colaborador'] or ""); gst = c4.text_input("Gestor:", l['Gestor'] or "")
                                    cc_e = st.selectbox("CC:", lista_cc, index=lista_cc.index(l['CC']) if l['CC'] in lista_cc else 0)
                                    if st.form_submit_button("Salvar"):
                                        with get_conn() as conn:
                                            with conn.cursor() as cur: cur.execute('UPDATE telefonia SET "Conta"=%s,"Operadora"=%s,"Colaborador"=%s,"CC"=%s,"Gestor"=%s WHERE "Numero"=%s', (cta, opr, clb, cc_e, gst, nf))
                                        st.success("Salvo!"); st.cache_data.clear()
                else:
                    ns = st.text_input("Número:")
                    if ns:
                        nf = formatar_numero(ns)
                        with get_conn() as conn:
                            with conn.cursor() as cur: cur.execute('SELECT "Status" FROM telefonia WHERE "Numero"=%s', (nf,)); r = cur.fetchone()
                        if r:
                            n_st = "Inativo" if r['Status'] == "Ativo" else "Ativo"
                            if st.button(f"Mudar para {n_st}"):
                                with get_conn() as conn:
                                    with conn.cursor() as cur: cur.execute('UPDATE telefonia SET "Status"=%s WHERE "Numero"=%s', (n_st, nf))
                                st.success("Alterado!"); st.cache_data.clear(); st.rerun()
            with tabs_tel[1]:
                st.download_button("⬇️ Template", gerar_template_telefonia(), "tel.xlsx")
                at = st.file_uploader("Arq (.xlsx):", type=["xlsx"])
                if at and st.button("Importar"):
                    dft = pd.read_excel(at, engine='openpyxl').fillna('')
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute('SELECT "Numero" FROM telefonia')
                            ndb = set(r['Numero'] for r in cur.fetchall())
                            i_t, u_t = [], []
                            for _, r in dft.iterrows():
                                nf = formatar_numero(r['Numero'])
                                if nf and r['Conta'] in CONTAS_TELEFONIA:
                                    s_v = str(r['Status']).capitalize() if str(r['Status']).capitalize() in STATUS_TELEFONIA else 'Ativo'
                                    if nf in ndb: u_t.append((r['Conta'], r['Operadora'], r['Colaborador'], r['CC'], s_v, r['Gestor'], nf))
                                    else: i_t.append((nf, r['Conta'], r['Operadora'], r['Colaborador'], r['CC'], s_v, r['Gestor'])); ndb.add(nf)
                            if i_t: cur.executemany('INSERT INTO telefonia ("Numero","Conta","Operadora","Colaborador","CC","Status","Gestor") VALUES (%s,%s,%s,%s,%s,%s,%s)', i_t)
                            if u_t: cur.executemany('UPDATE telefonia SET "Conta"=%s,"Operadora"=%s,"Colaborador"=%s,"CC"=%s,"Status"=%s,"Gestor"=%s WHERE "Numero"=%s', u_t)
                    st.success("Importação concluída!"); st.cache_data.clear(); st.rerun()

        elif modulo_ativo == "⚙️ Administração":
            tabs_admin = st.tabs(["👥 Usuários", "🏢 CCs", "👷 Colab", "🗑️ Limpeza (PIN)"])

            with tabs_admin[0]:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id, email, nome, nivel, cc_permitido FROM usuarios")
                        lista_u = cur.fetchall()
                a_u = st.radio("Ação:", ["➕ Criar Novo", "✏️ Editar", "🗑️ Excluir"], horizontal=True)
                if a_u == "➕ Criar Novo":
                    if st.button("🛠️ Corrigir Seq IDs"):
                        with get_conn() as conn:
                            with conn.cursor() as cur: cur.execute("SELECT setval('usuarios_id_seq', COALESCE((SELECT MAX(id)+1 FROM usuarios), 1), false);")
                        st.success("✅ Seq corrigida!")
                    with st.form("fu"):
                        u1, u2 = st.columns(2); e, s = u1.text_input("E-mail:"), u2.text_input("Senha:", type="password")
                        u3, u4 = st.columns(2)
                        # ── MELHORIA 4: NIVEIS_USUARIO sem Gestor ──
                        n = u3.selectbox("Nível:", NIVEIS_USUARIO)
                        c = u4.multiselect("CCs:", ["Todos"] + lista_cc, default=["Todos"])
                        if st.form_submit_button("Criar") and e and s:
                            try:
                                with get_conn() as conn:
                                    with conn.cursor() as cur: cur.execute('INSERT INTO usuarios (email, senha, nome, nivel, cc_permitido) VALUES (%s,%s,%s,%s,%s)', (e.lower().strip(), s, e.split('@')[0].capitalize(), n, "Todos" if "Todos" in c else "|".join(c)))
                                st.success("Criado!"); st.rerun()
                            except Exception as ex: st.error(f"Erro: {ex}")
                elif a_u == "✏️ Editar":
                    se = st.selectbox("Usuário:", [u['email'] for u in lista_u])
                    if se:
                        ud = next(u for u in lista_u if u['email'] == se)
                        with st.form("feu"):
                            st.write(f"Editando: **{ud['nome']}**")
                            # ── MELHORIA 4: migração automática Gestor→Almoxarife na edição ──
                            nivel_atual = ud['nivel'] if ud['nivel'] in NIVEIS_USUARIO else 'Almoxarife'
                            nn = st.selectbox("Nível:", NIVEIS_USUARIO, index=NIVEIS_USUARIO.index(nivel_atual))
                            cca = [cx for cx in ud['cc_permitido'].split('|') if cx in ["Todos"] + lista_cc] or ["Todos"]
                            nc = st.multiselect("CCs:", ["Todos"] + lista_cc, default=cca)
                            if st.form_submit_button("Salvar"):
                                with get_conn() as conn:
                                    with conn.cursor() as cur: cur.execute("UPDATE usuarios SET nivel=%s, cc_permitido=%s WHERE email=%s", (nn, "Todos" if "Todos" in nc else "|".join(nc), se))
                                st.success("Atualizado!"); st.rerun()
                elif a_u == "🗑️ Excluir":
                    oe = [u['email'] for u in lista_u if u['email'] != user['email']]
                    ue = st.selectbox("Excluir:", oe) if oe else None
                    if st.button("Confirmar Exclusão") and ue:
                        with get_conn() as conn:
                            with conn.cursor() as cur: cur.execute("DELETE FROM usuarios WHERE email=%s", (ue,))
                        st.success("Excluído!"); st.rerun()
                st.dataframe(pd.DataFrame(lista_u), hide_index=True)

            with tabs_admin[1]:
                c1, c2 = st.columns(2)
                with c1:
                    n_cc = st.text_input("Novo CC:")
                    if n_cc and aprovar_acao_master("add_cc", f"Criar CC {n_cc}"):
                        with get_conn() as conn:
                            with conn.cursor() as cur: cur.execute("INSERT INTO centros_custo (nome) VALUES (%s) ON CONFLICT DO NOTHING", (n_cc,))
                        st.success("Criado!"); st.cache_data.clear(); st.rerun()
                with c2:
                    ca, cn = st.selectbox("De:", lista_cc), st.text_input("Para:")
                    if cn and ca and aprovar_acao_master("ren_cc", f"Renomear CC {ca}"):
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute("INSERT INTO centros_custo (nome) VALUES (%s) ON CONFLICT DO NOTHING", (cn,))
                                cur.execute('UPDATE estoque SET "CC" = %s WHERE "CC" = %s', (cn, ca)); cur.execute('UPDATE telefonia SET "CC" = %s WHERE "CC" = %s', (cn, ca)); cur.execute("DELETE FROM centros_custo WHERE nome = %s", (ca,))
                        st.success("Renomeado!"); st.cache_data.clear(); st.rerun()
                st.divider()
                c3, c4 = st.columns(2)
                with c3:
                    st.download_button("⬇️ De/Para", gerar_template_depara(), "depara.xlsx")
                    ad = st.file_uploader("Arq De/Para:", type=["xlsx"])
                    if ad and aprovar_acao_master("dp_mass", "De/Para em Massa"):
                        df_d = pd.read_excel(ad)
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                for _, r in df_d.iterrows():
                                    d, p = str(r['De']).strip(), str(r['Para']).strip()
                                    if d != 'nan' and p != 'nan':
                                        cur.execute("INSERT INTO centros_custo (nome) VALUES (%s) ON CONFLICT DO NOTHING", (p,))
                                        cur.execute('UPDATE estoque SET "CC" = %s WHERE "CC" = %s', (p, d)); cur.execute('UPDATE telefonia SET "CC" = %s WHERE "CC" = %s', (p, d)); cur.execute("DELETE FROM centros_custo WHERE nome = %s", (d,))
                        st.success("Concluído!"); st.cache_data.clear(); st.rerun()
                with c4:
                    cm = st.text_area("CCs por linha:")
                    if cm and aprovar_acao_master("cc_mass", "Add CCs"):
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                for cx in [x.strip() for x in cm.split('\n') if x.strip()]: cur.execute("INSERT INTO centros_custo (nome) VALUES (%s) ON CONFLICT DO NOTHING", (cx,))
                        st.success("Add!"); st.cache_data.clear(); st.rerun()

            with tabs_admin[2]:
                c1, c2 = st.columns(2)
                with c1:
                    nc = st.text_input("Novo Colab:")
                    if st.button("Add Individual") and nc:
                        with get_conn() as conn:
                            with conn.cursor() as cur: cur.execute("INSERT INTO colaboradores (nome) VALUES (%s) ON CONFLICT DO NOTHING", (nc.strip().upper(),))
                        st.success("Add!"); st.cache_data.clear(); st.rerun()
                    lm = st.text_area("Colabs (um por linha):")
                    if st.button("Add em Massa") and lm:
                        ncs = [x.strip().upper() for x in lm.split('\n') if x.strip()]
                        with get_conn() as conn:
                            with conn.cursor() as cur: cur.executemany("INSERT INTO colaboradores (nome) VALUES (%s) ON CONFLICT DO NOTHING", [(x,) for x in ncs])
                        st.success("Add!"); st.cache_data.clear(); st.rerun()
                with c2:
                    if lista_colabs:
                        dc = st.selectbox("Remover:", lista_colabs)
                        if st.button("Excluir Colab"):
                            with get_conn() as conn:
                                with conn.cursor() as cur: cur.execute("DELETE FROM colaboradores WHERE nome = %s", (dc,))
                            st.success("Excluído!"); st.cache_data.clear(); st.rerun()

            with tabs_admin[3]:
                st.subheader("⚠️ Limpeza (Requer PIN)")
                c1, c2 = st.columns(2)
                with c1:
                    st.write("**Zerar Carga (Criar Devolução Automática)**")
                    if aprovar_acao_master("zc_all", "Zerar Carga de Todos"):
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    SELECT m.retirante_nome, m.cc_destino, mi.codigo_item, mi.descricao,
                                        SUM(CASE WHEN m.tipo = 'RDM' THEN mi.quantidade ELSE -mi.quantidade END) AS saldo
                                    FROM movimentacoes_itens mi JOIN movimentacoes m ON m.id = mi.movimentacao_id WHERE m.status = 'Aprovado'
                                    GROUP BY m.retirante_nome, m.cc_destino, mi.codigo_item, mi.descricao
                                    HAVING SUM(CASE WHEN m.tipo = 'RDM' THEN mi.quantidade ELSE -mi.quantidade END) > 0
                                """)
                                pends = cur.fetchall()
                                c_d = 0
                                for p in pends:
                                    cur.execute('''INSERT INTO movimentacoes (tipo, cc_destino, solicitante_email, retirante_nome, status, data_aprovacao, aprovador_email) VALUES ('CGM', %s, %s, %s, 'Aprovado', NOW(), %s) RETURNING id''', (p['cc_destino'], user['email'], p['retirante_nome'], user['email']))
                                    cid = cur.fetchone()['id']
                                    cur.execute('INSERT INTO movimentacoes_itens (movimentacao_id, codigo_item, quantidade, descricao) VALUES (%s, %s, %s, %s)', (cid, p['codigo_item'], p['saldo'], p['descricao']))
                                    cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" + %s WHERE "Codigo"=%s AND "CC"=%s', (p['saldo'], p['codigo_item'], p['cc_destino']))
                                    c_d += 1
                        st.success(f"{c_d} Devoluções automáticas criadas."); st.cache_data.clear(); st.rerun()
                    st.write("**Limpar Tabelas Físicas**")
                    oe = st.radio("Estoque:", ["Zerar qtds", "Apagar tudo"])
                    if aprovar_acao_master("l_e", f"Limpar Estoque ({oe})"):
                        with get_conn() as conn:
                            with conn.cursor() as cur: cur.execute('UPDATE estoque SET "Quantidade"=0' if "Zerar" in oe else "DELETE FROM estoque")
                        st.success("Estoque limpo!"); st.cache_data.clear(); st.rerun()
                with c2:
                    st.write("**Limpar Tabelas Secundárias**")
                    ot = st.radio("Telefonia:", ["Inativar", "Apagar tudo"])
                    if aprovar_acao_master("l_t", f"Limpar Tel ({ot})"):
                        with get_conn() as conn:
                            with conn.cursor() as cur: cur.execute("UPDATE telefonia SET \"Status\"='Inativo'" if "Inativar" in ot else "DELETE FROM telefonia")
                        st.success("Telefonia limpa!"); st.cache_data.clear(); st.rerun()
                    ce = st.text_input("Apagar Cód. Específico (Estoque):")
                    if ce and aprovar_acao_master("d_c", f"Apagar {ce}"):
                        # ── MELHORIA 1: zerar carga antes de apagar código específico ──
                        n_dev = zerar_carga_por_codigo(ce, user['email'])
                        with get_conn() as conn:
                            with conn.cursor() as cur: cur.execute('DELETE FROM estoque WHERE "Codigo"=%s', (ce,))
                        msg_extra = f" {n_dev} devolução(ões) automática(s) criada(s)." if n_dev > 0 else ""
                        st.success(f"Apagado!{msg_extra}"); st.cache_data.clear(); st.rerun()
