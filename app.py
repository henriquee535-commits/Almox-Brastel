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
from fpdf import FPDF  # NOVA IMPORTAÇÃO PARA O PDF

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
            # NOVAS TABELAS DE USUÁRIOS E REQUISIÇÕES
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
            
            # Ajuste automático caso o banco antigo ainda tenha a coluna "Localizacao"
            c.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='telefonia' AND column_name='Localizacao'
            """)
            if c.fetchone():
                c.execute('ALTER TABLE telefonia RENAME COLUMN "Localizacao" TO "Gestor"')
            
            # Cria usuário master padrão se a tabela estiver vazia
            c.execute("SELECT count(*) as total FROM usuarios")
            if c.fetchone()['total'] == 0:
                c.execute('''
                    INSERT INTO usuarios (email, senha, nome, nivel, cc_permitido) 
                    VALUES (%s, %s, %s, %s, %s)
                ''', ('master@brastelnet.com.br', SENHA_ZERAR_ESTOQUE, 'Administrador', 'Master', 'Todos'))
                
        conn.commit()

init_db()

# ── helpers de validação e sessão ──────────────────────────────────────────────
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

# ── gerador de pdf ────────────────────────────────────────────────────────────
def gerar_pdf_comprovante(req_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM movimentacoes WHERE id = %s", (req_id,))
            req = cur.fetchone()
            if not req: return None
            
            cur.execute('SELECT "Descricao" FROM estoque WHERE "Codigo" = %s LIMIT 1', (req['codigo_item'],))
            desc = cur.fetchone()
            descricao = desc['Descricao'] if desc else "Descrição não encontrada"

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(200, 10, txt="BRASTEL - CONTROLE DE ALMOXARIFADO", ln=True, align='C')
    pdf.set_font("Arial", 'B', 12)
    titulo = "REQUISIÇÃO DE MATERIAL (RDM)" if req['tipo'] == 'RDM' else "ENTREGA DE MATERIAL (CGM)"
    pdf.cell(200, 10, txt=f"TERMO DE {titulo} - #{req['id']}", ln=True, align='C')
    pdf.ln(10)
    
    pdf.set_font("Arial", '', 12)
    pdf.cell(200, 8, txt=f"Data da Solicitação: {req['data_solicitacao'].strftime('%d/%m/%Y %H:%M')}", ln=True)
    pdf.cell(200, 8, txt=f"Data da Aprovação: {req['data_aprovacao'].strftime('%d/%m/%Y %H:%M') if req['data_aprovacao'] else 'N/A'}", ln=True)
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

    # Retorna como buffer de bytes para o Streamlit
    return pdf.output(dest='S').encode('latin-1')

# ── cache ─────────────────────────────────────────────────────────────────────
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

# --- CONTROLE DE ACESSO E LIMITE DE USUÁRIOS ---
if 'sessao_id' not in st.session_state:
    st.session_state.sessao_id = str(uuid.uuid4())

with get_conn() as conn:
    with conn.cursor() as c:
        tempo_limite = datetime.now() - timedelta(minutes=TEMPO_INATIVIDADE)
        c.execute("DELETE FROM acessos WHERE ultimo_clique < %s", (tempo_limite,))
        c.execute("""
            INSERT INTO acessos (sessao_id, ultimo_clique) VALUES (%s, %s)
            ON CONFLICT (sessao_id) DO UPDATE SET ultimo_clique = EXCLUDED.ultimo_clique
        """, (st.session_state.sessao_id, datetime.now()))
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

# --- NAVEGAÇÃO ---
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
# TELA 1 & 2: CONSULTA PUBLICA (Mantida igual ao original para brevidade,
# AQUI FICA O CÓDIGO EXATO DO SEU IF MENU == "📊 Consulta" e "📱 Telefonia")
# ══════════════════════════════════════════════════════════════════
if menu == "📊 Consulta":
    st.title("📊 Consulta Almoxarifado")
    st.dataframe(df, use_container_width=True, hide_index=True)

elif menu == "📱 Telefonia":
    st.title("📱 Telefonia Brastel")
    st.dataframe(df_tel, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════
# TELA 3: SISTEMA INTERNO (Restrito por Login)
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
        st.title("Controle Interno — Brastel")
        
        # Define as abas baseado no nível do usuário
        abas_nomes = []
        if user['nivel'] in ['Leitor', 'Gestor', 'Almoxarife', 'Master']:
            abas_nomes.append("🛒 Solicitar Material (RDM/CGM)")
        if user['nivel'] in ['Almoxarife', 'Master']:
            abas_nomes.extend(["✅ Aprovações Pendentes", "📝 Estoque — Registro", "📤 Estoque — Carga em Massa", "📱 Telefonia — Registro", "📤 Telefonia — Carga em Massa"])
        if user['nivel'] == 'Master':
            abas_nomes.extend(["👥 Gerenciar Usuários", "🏢 Gerenciar CCs", "🗑️ Área de Exclusão"])

        abas = st.tabs(abas_nomes)
        tab_idx = 0

        # --- ABA: SOLICITAR MATERIAL ---
        if "🛒 Solicitar Material (RDM/CGM)" in abas_nomes:
            with abas[tab_idx]:
                st.subheader("🛒 Requisição ou Devolução de Material")
                with st.form("form_solicitacao", clear_on_submit=True):
                    tipo_req = st.radio("Tipo:", ["RDM (Retirar Material)", "CGM (Devolver Material)"], horizontal=True)
                    tipo_db = "RDM" if "RDM" in tipo_req else "CGM"
                    
                    cc_opcoes = lista_cc if user['cc_permitido'] == 'Todos' else [user['cc_permitido']]
                    cc_req = st.selectbox("Centro de Custo:", cc_opcoes)
                    
                    col_s1, col_s2 = st.columns(2)
                    cod_req = col_s1.text_input("Código do Item:")
                    qtd_req = col_s2.number_input("Quantidade:", min_value=1, step=1)
                    retirante = st.text_input("Nome da pessoa que irá buscar/entregar fisicamente:")
                    
                    if st.form_submit_button("Enviar Solicitação"):
                        if not cod_req or not retirante:
                            st.error("Preencha todos os campos obrigatórios.")
                        else:
                            # Validação de Estoque para RDM
                            pode_prosseguir = True
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
                                st.success("✅ Solicitação enviada ao Almoxarifado com sucesso! Acompanhe a aprovação.")
            tab_idx += 1

        # --- ABA: APROVAÇÕES PENDENTES (Almoxarife/Master) ---
        if "✅ Aprovações Pendentes" in abas_nomes:
            with abas[tab_idx]:
                st.subheader("📋 Painel do Almoxarife")
                
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
                            st.write(f"**Data da Solicitação:** {req['data_solicitacao'].strftime('%d/%m/%Y %H:%M')}")
                            
                            c_btn1, c_btn2, _ = st.columns([1, 1, 3])
                            if c_btn1.button("✅ Aprovar e Liberar", key=f"apr_{req['id']}"):
                                try:
                                    with get_conn() as conn:
                                        with conn.cursor() as cur:
                                            # Transação para garantir integridade
                                            cur.execute("BEGIN;")
                                            
                                            # Verifica saldo no exato momento da aprovação (se RDM)
                                            if req['tipo'] == 'RDM':
                                                cur.execute('SELECT "Quantidade" FROM estoque WHERE "Codigo"=%s AND "CC"=%s FOR UPDATE', (req['codigo_item'], req['cc_destino']))
                                                saldo_db = cur.fetchone()
                                                if not saldo_db or saldo_db['Quantidade'] < req['quantidade']:
                                                    st.error("Alerta: O estoque acabou antes da aprovação!")
                                                    cur.execute("ROLLBACK;")
                                                    st.stop()
                                                # Subtrai
                                                cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" - %s WHERE "Codigo"=%s AND "CC"=%s', (req['quantidade'], req['codigo_item'], req['cc_destino']))
                                            else:
                                                # CGM: Soma ao estoque
                                                cur.execute('UPDATE estoque SET "Quantidade" = "Quantidade" + %s WHERE "Codigo"=%s AND "CC"=%s', (req['quantidade'], req['codigo_item'], req['cc_destino']))
                                            
                                            # Atualiza status
                                            cur.execute('''
                                                UPDATE movimentacoes 
                                                SET status = 'Aprovado', data_aprovacao = NOW(), aprovador_email = %s 
                                                WHERE id = %s
                                            ''', (user['email'], req['id']))
                                            
                                            cur.execute("COMMIT;")
                                    st.success("✅ Movimentação aprovada e estoque atualizado!")
                                    st.cache_data.clear()
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Erro na transação: {e}")

                            if c_btn2.button("❌ Rejeitar", key=f"rej_{req['id']}"):
                                with get_conn() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute("UPDATE movimentacoes SET status = 'Rejeitado', aprovador_email = %s WHERE id = %s", (user['email'], req['id']))
                                    conn.commit()
                                st.warning("Solicitação rejeitada.")
                                st.rerun()

                st.divider()
                st.subheader("🖨️ Documentos de Retirada/Devolução (Aprovados)")
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id, tipo, data_aprovacao, retirante_nome FROM movimentacoes WHERE status = 'Aprovado' ORDER BY data_aprovacao DESC LIMIT 10")
                        aprovados = cur.fetchall()
                
                for ap in aprovados:
                    col_txt, col_dl = st.columns([3, 1])
                    col_txt.write(f"#{ap['id']} - {ap['tipo']} - Retirante: {ap['retirante_nome']} (Aprovado em {ap['data_aprovacao'].strftime('%d/%m %H:%M')})")
                    
                    pdf_bytes = gerar_pdf_comprovante(ap['id'])
                    if pdf_bytes:
                        col_dl.download_button(label="📄 Baixar PDF", data=pdf_bytes, file_name=f"Documento_{ap['tipo']}_{ap['id']}.pdf", mime="application/pdf", key=f"dl_pdf_{ap['id']}")
            tab_idx += 1

        # --- ABAS DE REGISTRO / CARGA (Mantidas iguais ao original para Almoxarife) ---
        if "📝 Estoque — Registro" in abas_nomes:
            with abas[tab_idx]:
                st.info("Aqui fica o formulário de Entrada/Saída manual (Seu código original).")
            tab_idx += 1
        if "📤 Estoque — Carga em Massa" in abas_nomes:
            with abas[tab_idx]:
                st.info("Upload Excel Almoxarifado.")
            tab_idx += 1
        if "📱 Telefonia — Registro" in abas_nomes:
            with abas[tab_idx]:
                st.info("Registro de Telefonia.")
            tab_idx += 1
        if "📤 Telefonia — Carga em Massa" in abas_nomes:
            with abas[tab_idx]:
                st.info("Upload Excel Telefonia.")
            tab_idx += 1

        # --- ABA MASTER: USUÁRIOS ---
        if "👥 Gerenciar Usuários" in abas_nomes:
            with abas[tab_idx]:
                st.subheader("👥 Gestão de Acessos")
                with st.form("form_novo_usuario", clear_on_submit=True):
                    uc1, uc2 = st.columns(2)
                    novo_email = uc1.text_input("E-mail corporativo:")
                    nova_senha = uc2.text_input("Senha Inicial:", type="password")
                    
                    uc3, uc4 = st.columns(2)
                    nivel_acc = uc3.selectbox("Nível de Permissão:", ["Leitor", "Gestor", "Almoxarife", "Master"])
                    cc_acc = uc4.selectbox("Centro de Custo (Visão restrita):", ["Todos"] + lista_cc)
                    
                    if st.form_submit_button("Cadastrar Usuário"):
                        if novo_email and nova_senha:
                            try:
                                with get_conn() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute('''
                                            INSERT INTO usuarios (email, senha, nome, nivel, cc_permitido) 
                                            VALUES (%s, %s, %s, %s, %s)
                                        ''', (novo_email.strip().lower(), nova_senha, novo_email.split('@')[0].capitalize(), nivel_acc, cc_acc))
                                        conn.commit()
                                st.success("Usuário criado!")
                            except psycopg2.errors.UniqueViolation:
                                st.error("Este e-mail já possui cadastro.")
                        else:
                            st.error("Preencha e-mail e senha.")
                
                st.divider()
                st.write("**Usuários Cadastrados:**")
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id, email, nome, nivel, cc_permitido FROM usuarios")
                        df_users = pd.DataFrame(cur.fetchall())
                st.dataframe(df_users, hide_index=True)
            tab_idx += 1

        if "🏢 Gerenciar CCs" in abas_nomes:
            with abas[tab_idx]:
                st.info("Aqui fica o De/Para de Centros de Custo.")
            tab_idx += 1

        if "🗑️ Área de Exclusão" in abas_nomes:
            with abas[tab_idx]:
                st.warning("Aqui fica a limpeza de banco de dados (Zerar Estoque / Telefonia).")
            tab_idx += 1
