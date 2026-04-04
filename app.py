# ==========================================
# TELA 1: CONSULTA (PÚBLICA)
# ==========================================
if menu == "📊 Consulta (Público)":
    
    # 1. Cabeçalho com Dois Logos
    col_logo1, col_titulo, col_logo2 = st.columns([1, 6, 1])
    with col_logo1:
        try: st.image("logo1.png", use_container_width=True)
        except: pass
            
    with col_titulo:
        st.title("Painel de Estoque")
        
    with col_logo2:
        try: st.image("logo2.png", use_container_width=True)
        except: pass
    
    st.divider()
    
    # 2. Métricas
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📦 Peças em Estoque", f"{df['Quantidade'].sum():.0f}" if not df.empty else 0)
    col2.metric("🏷️ Itens Cadastrados", df['Codigo'].nunique() if not df.empty else 0)
    col3.metric("🏢 Setores", df['CC'].nunique() if not df.empty else 0)
    col4.metric("👁️ Pessoas Online", f"{total_ativos} / {LIMITE_PESSOAS}")
    
    st.divider()
    
    # 3. Área de Filtros Dinâmicos
    col_busca, col_filtro = st.columns([3, 1])
    busca = col_busca.text_input("🔍 Buscar Código ou Descrição:")
    setor_filtro = col_filtro.selectbox("🏢 Filtrar por Setor:", ["Todos"] + lista_cc)

    # Processando os filtros
    df_exibir = df.copy()
    if busca:
        df_exibir = df_exibir[df_exibir['Codigo'].str.contains(busca, case=False, na=False) | 
                              df_exibir['Descricao'].str.contains(busca, case=False, na=False)]
    if setor_filtro != "Todos":
        df_exibir = df_exibir[df_exibir['CC'] == setor_filtro]
        
    # 4. Abas de Navegação (Tabela vs Gráficos)
    aba1, aba2 = st.tabs(["📋 Tabela de Dados", "📈 Visão Gráfica"])
    
    with aba1:
        st.dataframe(
            df_exibir, 
            use_container_width=True, 
            hide_index=True,
            column_config={
                "Quantidade": st.column_config.NumberColumn("Quantidade", format="%.0f")
            }
        )
        
    with aba2:
        if not df_exibir.empty:
            st.subheader("Volume de Estoque por Setor")
            st.bar_chart(df_exibir.groupby('CC')['Quantidade'].sum())
        else:
            st.info("Nenhum dado para exibir no gráfico.")
