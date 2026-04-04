import streamlit as st
import pandas as pd
import sqlite3
import uuid
from datetime import datetime, timedelta
import io

# 1. CONFIGURAÇÃO DA PÁGINA
st.set_page_config(page_title="Almoxarifado Pro", layout="wide", page_icon="📦")

# --- CSS PERSONALIZADO: header com logos mais bonito ---
st.markdown("""
<style>
/* Importa fonte elegante */
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Sora', sans-serif;
}

/* Header customizado */
.header-container {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 18px 32px;
    background: linear-gradient(135deg, #0f2027 0%, #1a3a4a 60%, #0d3349 100%);
    border-radius: 16px;
    margin-bottom: 24px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.18);
}

.header-logos {
    display: flex;
    align-items: center;
    gap: 14px;
}

.header-logo-box {
    background: rgba(255,255,255,0.10);
    border-radius: 10px;
    padding: 8px 16px;
    display: flex;
    align-items: center;
    justify-content: center;
    min-width: 80px;
    min-height: 48px;
    font-size: 0.85rem;
    color: #a0c4d8;
    font-weight: 600;
    letter-spacing: 0.05em;
    border: 1px solid rgba(255,255,255,0.10);
}

.header-title-block {
    text-align: center;
    flex: 1;
}

.header-title-block h1 {
    font-size: 2rem;
    font-weight: 700;
    color: #ffffff;
    margin: 0;
    letter-spacing: -0.01em;
    line-height: 1.1;
}

.header-title-block p {
    font-size: 0.85rem;
    color: #7eb8d4;
    margin: 4px 0 0 0;
    letter-spacing: 0.12em;
    text-transform: uppercase;
}

.header-badge {
    background: rgba(126,184,212,0.15);
    border: 1px solid rgba(126,184,212,0.3);
    color: #7eb8d4;
    border-radius: 20px;
    padding: 4px 14px;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.08em;
}

/* Métricas */
[data-testid="metric-container"] {
    background: #f7fafc;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 16px 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
}

/* Botão primário */
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
.stButton > button:hover {
    opacity: 0.88;
    color: white;
}

/* Alerta de sucesso */
.stAlert {
    border-radius: 10px;
}
</style>
""", unsafe_allow_html=True)

# --- CONFIGURAÇÕES ---
ARQUIVO_PLANILHA = 'Almoxarifado.xlsm'
SENHA_ACESSO = "1234"
DB_NAME = 'estoque.db'
LIMITE_PESSOAS = 40
TEMPO_INATIVIDADE = 1  # Minutos

# --- BANCO DE DADOS ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS estoque 
                 (Codigo TEXT, Descricao TEXT, Quantidade REAL, CC TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS acessos 
                 (sessao_id TEXT PRIMARY KEY, ultimo_clique TIMESTAMP)''')
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

def buscar_descricao_por_codigo(cod):
    """Retorna a descrição já cadastrada para um código, ou None se não existir."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT DISTINCT Descricao FROM estoque WHERE Codigo=?", (cod,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

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

    # Header bonito com logos
    st.markdown(f"""
    <div class="header-container">
        <div class="header-logos">
            <div class="header-logo-box">🔷 Brastel</div>
        </div>
        <div class="header-title-block">
            <h1>📦 Painel de Estoque</h1>
            <p>Almoxarifado Pro · Controle de Inventário</p>
        </div>
        <div class="header-logos">
            <div class="header-logo-box">🌀 Engia</div>
            <span class="header-badge">🟢 {total_ativos}/{LIMITE_PESSOAS} online</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Nota: substitua os textos "🔷 Brastel" e "🌀 Engia" por:
    # <img src="logo1.png" height="40" style="border-radius:6px">
    # quando suas logos estiverem disponíveis no servidor.

    m1, m2, m3 = st.columns(3)
    m1.metric("📦 Total de Peças", f"{df['Quantidade'].sum():.0f}" if not df.empty else 0)
    m2.metric("🏷️ Itens Únicos", df['Codigo'].nunique() if not df.empty else 0)
    m3.metric("🏢 Setores", df['CC'].nunique() if not df.empty else 0)

    st.divider()
    busca = st.text_input("🔍 Pesquisar Código ou Descrição:")
    df_filt = df.copy()
    if busca:
        df_filt = df[
            df['Codigo'].astype(str).str.contains(busca, case=False) |
            df['Descricao'].str.contains(busca, case=False, na=False)
        ]
    st.dataframe(df_filt, use_container_width=True, hide_index=True)

# ==========================================
# TELA 2: ALMOXARIFADO
# ==========================================
else:
    st.title("🔒 Área Restrita — Almoxarifado")
    senha = st.text_input("Senha:", type="password")

    if senha == SENHA_ACESSO:

        tab1, tab2 = st.tabs(["📝 Registro Individual", "📤 Carga em Massa"])

        # ------------------------------------------
        # TAB 1: REGISTRO INDIVIDUAL
        # ------------------------------------------
        with tab1:
            st.subheader("Registrar Entrada / Saída")
            with st.form("registro"):
                c1, c2 = st.columns(2)
                cod = c1.text_input("Código:")
                desc_input = c2.text_input("Descrição (somente para itens novos):")

                c3, c4, c5 = st.columns([2, 2, 1])
                cc_sel = c3.selectbox("Setor (Centro de Custo):", lista_cc)
                op = c4.selectbox("Operação:", ["Entrada", "Saída"])
                qtd = c5.number_input("Qtd:", min_value=1.0)

                submitted = st.form_submit_button("✅ Confirmar")

            if submitted:
                if not cod:
                    st.error("Informe o Código do item.")
                else:
                    # Verifica se código já existe com outra descrição
                    desc_existente = buscar_descricao_por_codigo(cod)

                    if desc_existente and desc_input and desc_input.strip() != desc_existente.strip():
                        st.error(
                            f"⛔ Conflito de Descrição!\n\n"
                            f"O código **{cod}** já está cadastrado com a descrição:\n\n"
                            f"**\"{desc_existente}\"**\n\n"
                            f"Não é permitido cadastrar o mesmo código com descrições diferentes. "
                            f"Deixe o campo 'Descrição' em branco para usar a descrição existente, "
                            f"ou corrija o código."
                        )
                    else:
                        # Usa a descrição já existente se não foi digitada nova
                        desc_final = desc_existente if desc_existente else desc_input

                        conn = sqlite3.connect(DB_NAME)
                        cur = conn.cursor()
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
                        st.rerun()

        # ------------------------------------------
        # TAB 2: CARGA EM MASSA
        # ------------------------------------------
        with tab2:
            st.subheader("📤 Importar Inventário em Massa")

            st.info(
                "Faça upload de um arquivo **CSV** ou **Excel (.xlsx)** com as colunas:\n\n"
                "`Codigo` | `Descricao` | `Quantidade` | `CC`\n\n"
                "• A operação padrão é **Entrada** (soma ao estoque existente).\n"
                "• Se o código já existir com descrição diferente, a linha será **ignorada** e reportada."
            )

            # Botão para baixar template
            template_df = pd.DataFrame({
                'Codigo': ['ABC001', 'ABC002'],
                'Descricao': ['Parafuso M8', 'Cabo Elétrico 2,5mm'],
                'Quantidade': [100, 50],
                'CC': ['Setor Geral', 'Manutenção']
            })
            template_csv = template_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                "⬇️ Baixar Template CSV",
                data=template_csv,
                file_name="template_inventario.csv",
                mime="text/csv"
            )

            arquivo = st.file_uploader(
                "Selecione o arquivo:",
                type=["csv", "xlsx"],
                key="upload_massa"
            )

            if arquivo:
                try:
                    if arquivo.name.endswith('.csv'):
                        df_upload = pd.read_csv(arquivo)
                    else:
                        df_upload = pd.read_excel(arquivo, engine='openpyxl')

                    # Valida colunas obrigatórias
                    colunas_necessarias = {'Codigo', 'Descricao', 'Quantidade', 'CC'}
                    colunas_faltando = colunas_necessarias - set(df_upload.columns)
                    if colunas_faltando:
                        st.error(f"⛔ Colunas ausentes no arquivo: {', '.join(colunas_faltando)}")
                    else:
                        df_upload['Codigo'] = df_upload['Codigo'].astype(str).str.strip()
                        df_upload['Descricao'] = df_upload['Descricao'].astype(str).str.strip()
                        df_upload['CC'] = df_upload['CC'].astype(str).str.strip()
                        df_upload['Quantidade'] = pd.to_numeric(df_upload['Quantidade'], errors='coerce').fillna(0)

                        st.write(f"**{len(df_upload)} linhas encontradas.** Pré-visualização:")
                        st.dataframe(df_upload.head(10), use_container_width=True, hide_index=True)

                        if st.button("🚀 Processar Importação"):
                            conn = sqlite3.connect(DB_NAME)
                            cur = conn.cursor()

                            ok = 0
                            conflitos = []
                            ignorados = []

                            for _, row in df_upload.iterrows():
                                cod_r = row['Codigo']
                                desc_r = row['Descricao']
                                qtd_r = float(row['Quantidade'])
                                cc_r = row['CC']

                                # Checa conflito de descrição
                                cur.execute(
                                    "SELECT DISTINCT Descricao FROM estoque WHERE Codigo=?",
                                    (cod_r,)
                                )
                                desc_db = cur.fetchone()

                                if desc_db and desc_db[0].strip() != desc_r.strip():
                                    conflitos.append({
                                        'Codigo': cod_r,
                                        'Desc no Arquivo': desc_r,
                                        'Desc no Sistema': desc_db[0]
                                    })
                                    continue

                                if qtd_r <= 0:
                                    ignorados.append(cod_r)
                                    continue

                                # Atualiza ou insere
                                cur.execute(
                                    "SELECT Quantidade FROM estoque WHERE Codigo=? AND CC=?",
                                    (cod_r, cc_r)
                                )
                                res = cur.fetchone()
                                if res:
                                    novo = res[0] + qtd_r
                                    cur.execute(
                                        "UPDATE estoque SET Quantidade=? WHERE Codigo=? AND CC=?",
                                        (novo, cod_r, cc_r)
                                    )
                                else:
                                    cur.execute(
                                        "INSERT INTO estoque VALUES (?,?,?,?)",
                                        (cod_r, desc_r, qtd_r, cc_r)
                                    )
                                ok += 1

                            conn.commit()
                            conn.close()

                            st.success(f"✅ **{ok} itens** importados com sucesso!")

                            if conflitos:
                                st.warning(f"⚠️ **{len(conflitos)} linha(s) ignorada(s)** por conflito de descrição:")
                                st.dataframe(
                                    pd.DataFrame(conflitos),
                                    use_container_width=True,
                                    hide_index=True
                                )

                            if ignorados:
                                st.info(f"ℹ️ {len(ignorados)} linha(s) ignorada(s) por quantidade zero ou inválida: {', '.join(ignorados)}")

                            st.rerun()

                except Exception as e:
                    st.error(f"Erro ao processar arquivo: {e}")
