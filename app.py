import streamlit as st
import pandas as pd
import sqlite3
import os

st.set_page_config(page_title="Almoxarifado", layout="wide", page_icon="📦")

# --- CONFIGURAÇÕES ---
ARQUIVO_PLANILHA = 'Almoxarifado.xlsm' 
SENHA_ACESSO = "Almoxarifado123" 
DB_NAME = 'estoque.db'

# --- 1. BANCO DE DADOS ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS estoque (Codigo TEXT, Descricao TEXT, Quantidade REAL, CC TEXT)''')
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
        return pd.read_excel(ARQUIVO_PLANILHA, sheet_name='BD', engine='openpyxl')['Centro de Custo'].dropna().unique().tolist()
    except:
        return ["Erro na planilha"]

lista_cc = carregar_ccs()
df = carregar_estoque()

# --- MENU LATERAL ---
menu = st.sidebar.radio("Navegação", ["📊 Consulta (Público)", "🔒 Almoxarifado (Restrito)"])

# ==========================================
# TELA 1: CONSULTA (PÚBLICA)
# ==========================================
if menu == "📊 Consulta (Público)":
    col_logo, col_titulo = st.columns([1, 6])
    with col_logo:
        try: st.image("logo.png", use_container_width=True)
        except: pass
            
    with col_titulo:
        st.title("Painel de Estoque")
    
    st.divider()
    
    col1, col2, col3 = st.columns(3)
    col1.metric("📦 Peças em Estoque", df['Quantidade'].sum() if not df.empty else 0)
    col2.metric("🏷️ Itens Cadastrados", df['Codigo'].nunique() if not df.empty else 0)
    col3.metric("🏢 Setores", df['CC'].nunique() if not df.empty else 0)
    
    st.divider()
    
    busca = st.text_input("🔍 Buscar Código ou Descrição:")
    df_exibir = df[df['Codigo'].str.contains(busca, case=False, na=False) | df['Descricao'].str.contains(busca, case=False, na=False)] if busca else df
        
    st.dataframe(df_exibir, use_container_width=True, hide_index=True)

# ==========================================
# TELA 2: ALMOXARIFADO (RESTRITA)
# ==========================================
elif menu == "🔒 Almoxarifado (Restrito)":
    st.title("Gestão do Almoxarifado")
    senha = st.text_input("🔑 Senha de acesso:", type="password", width=300)
    
    if senha == SENHA_ACESSO:
        st.divider()
        st.subheader("📝 Registrar Movimentação")
        
        with st.form("form_movimentacao", clear_on_submit=True):
            col1, col2 = st.columns(2)
            codigo = col1.text_input("Código do Item:")
            descricao = col2.text_input("Descrição (Deixe em branco se já existe):")
            
            col3, col4, col5 = st.columns([2, 2, 1])
            cc = col3.selectbox("Setor (CC):", lista_cc)
            operacao = col4.selectbox("Operação:", ["Entrada (+)", "Saída (-)"])
            qtd = col5.number_input("Quantidade:", min_value=1.0, step=1.0)

            submit = st.form_submit_button("Salvar Registro", type="primary")

            if submit:
                if not codigo:
                    st.warning("Digite o código.")
                else:
                    codigo_limpo, cc_limpo = str(codigo).strip(), str(cc).strip()
                    conn = sqlite3.connect(DB_NAME)
                    c = conn.cursor()
                    
                    c.execute("SELECT Descricao FROM estoque WHERE Codigo=? LIMIT 1", (codigo_limpo,))
                    desc_banco = c.fetchone()
                    desc_padrao = desc_banco[0] if desc_banco else None
                    
                    c.execute("SELECT Quantidade FROM estoque WHERE Codigo=? AND CC=?", (codigo_limpo, cc_limpo))
                    resultado = c.fetchone()
                    
                    if resultado: 
                        saldo_atual = resultado[0]
                        if operacao == "Entrada (+)":
                            c.execute("UPDATE estoque SET Quantidade=? WHERE Codigo=? AND CC=?", (saldo_atual + qtd, codigo_limpo, cc_limpo))
                            st.success("Entrada registrada!")
                        else:
                            if saldo_atual < qtd: st.error("Saldo insuficiente!")
                            else:
                                c.execute("UPDATE estoque SET Quantidade=? WHERE Codigo=? AND CC=?", (saldo_atual - qtd, codigo_limpo, cc_limpo))
                                st.success("Saída registrada!")
                    else: 
                        if operacao == "Saída (-)": st.error("Erro: Sem saldo neste setor.")
                        else:
                            if desc_padrao:
                                c.execute("INSERT INTO estoque (Codigo, Descricao, Quantidade, CC) VALUES (?, ?, ?, ?)", (codigo_limpo, desc_padrao, qtd, cc_limpo))
                                st.success("Item adicionado ao novo setor!")
                            elif not descricao: st.error("Item inédito! Preencha a Descrição.")
                            else:
                                c.execute("INSERT INTO estoque (Codigo, Descricao, Quantidade, CC) VALUES (?, ?, ?, ?)", (codigo_limpo, descricao, qtd, cc_limpo))
                                st.success("Novo item cadastrado!")
                    
                    conn.commit()
                    conn.close()
                    st.rerun()

        st.divider()
        
        # --- NOVA FUNÇÃO: IMPORTAÇÃO EM MASSA ---
        with st.expander("📥 Importar Inventário em Massa (Excel/CSV)"):
            st.info("Sua planilha deve conter as colunas: **Codigo**, **Descricao**, **Quantidade** e **CC**.")
            arquivo = st.file_uploader("Selecione a planilha", type=["xlsx", "csv"])
            
            if arquivo is not None:
                if st.button("Processar Importação"):
                    try:
                        if arquivo.name.endswith('.csv'):
                            df_import = pd.read_csv(arquivo, dtype={'Codigo': str})
                        else:
                            df_import = pd.read_excel(arquivo, engine='openpyxl', dtype={'Codigo': str})
                            
                        colunas_req = ['Codigo', 'Descricao', 'Quantidade', 'CC']
                        if all(col in df_import.columns for col in colunas_req):
                            conn = sqlite3.connect(DB_NAME)
                            c = conn.cursor()
                            
                            # Limpa os códigos (remove .0 se houver)
                            df_import['Codigo'] = df_import['Codigo'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                            
                            for index, row in df_import.iterrows():
                                cod_imp = str(row['Codigo'])
                                desc_imp = str(row['Descricao'])
                                qtd_imp = float(row['Quantidade'])
                                cc_imp = str(row['CC']).strip()
                                
                                c.execute("SELECT Quantidade FROM estoque WHERE Codigo=? AND CC=?", (cod_imp, cc_imp))
                                res = c.fetchone()
                                
                                if res:
                                    # Se existir, soma a quantidade (Entrada)
                                    c.execute("UPDATE estoque SET Quantidade=? WHERE Codigo=? AND CC=?", (res[0] + qtd_imp, cod_imp, cc_imp))
                                else:
                                    # Se não existir, insere novo
                                    c.execute("INSERT INTO estoque (Codigo, Descricao, Quantidade, CC) VALUES (?, ?, ?, ?)", (cod_imp, desc_imp, qtd_imp, cc_imp))
                                    
                            conn.commit()
                            conn.close()
                            st.success("Inventário importado e atualizado com sucesso!")
                            st.rerun()
                        else:
                            st.error(f"Erro: As colunas devem ter exatamente estes nomes: {', '.join(colunas_req)}")
                    except Exception as e:
                        st.error(f"Erro ao ler o arquivo: {e}")

        # --- EXCLUIR REGISTRO ---
        with st.expander("🗑️ Excluir ou Corrigir um Registro"):
            st.warning("Atenção: Isso apagará o item do setor selecionado.")
            with st.form("form_excluir"):
                cod_ex = st.text_input("Código a excluir:")
                cc_ex = st.selectbox("Setor (CC):", lista_cc, key="cc_excluir")
                btn_excluir = st.form_submit_button("Apagar Registro Definitivamente")
                
                if btn_excluir and cod_ex:
                    conn = sqlite3.connect(DB_NAME)
                    c = conn.cursor()
                    c.execute("DELETE FROM estoque WHERE Codigo=? AND CC=?", (str(cod_ex).strip(), str(cc_ex).strip()))
                    conn.commit()
                    conn.close()
                    st.success("Item removido com sucesso!")
                    st.rerun()

    elif senha != "":
        st.error("Senha incorreta.")
