import streamlit as st
import pandas as pd
import json
from pathlib import Path

# 1. CONFIGURACIÓN DE PÁGINA
st.set_page_config(page_title="Monitor Pricing | Dipisa", layout="wide", page_icon="📊")

# --- CARGA DE DATOS ---
BASE_DIR = Path(__file__).parent
DATOS_PATH = BASE_DIR / "datos_procesados.json"
PRODUCTOS_PATH = BASE_DIR / "productos.csv"

@st.cache_data(ttl=60)
def cargar_datos():
    try:
        # Cargar catálogo de productos ignorando las líneas con #
        df_prod = pd.read_csv(PRODUCTOS_PATH, comment="#")
        
        # Cargar precios procesados por el bot
        with open(DATOS_PATH, "r", encoding="utf-8") as f:
            precios = json.load(f)
        df_precios = pd.DataFrame(precios)
        
        # Unir ambas tablas por el SKU interno para no perder ningún producto
        if not df_prod.empty:
            if not df_precios.empty:
                df = pd.merge(df_prod, df_precios, on="sku_interno", how="left")
            else:
                df = df_prod.copy()
            return df
    except Exception as e:
        st.error(f"Error cargando datos: {e}")
    return pd.DataFrame()

df = cargar_datos()

# 2. CABECERA CORPORATIVA CON LOGO
col_logo, col_titulo = st.columns([1, 4])
with col_logo:
    try:
        st.image("logo.png", width=180) 
    except:
        st.warning("⚠️ Falta subir logo.png a GitHub")
with col_titulo:
    st.title("Monitor de Pricing en Vivo")
    st.markdown("##### Inteligencia Competitiva Comercial — Dipisa & Ovella")

# Mostrar fecha de última actualización global
if not df.empty and "fecha_act" in df.columns:
    ultima_act = df["fecha_act"].dropna().iloc[0] if not df["fecha_act"].dropna().empty else "Desconocida"
    st.info(f"🕒 **Último informe generado por el bot:** {ultima_act}")
else:
    st.info("🕒 Esperando datos del bot...")

st.divider()

if not df.empty:
    # 3. MANEJO SEGURO DE ESTADOS Y CÁLCULOS
    if "estado" in df.columns:
        df["estado"] = df["estado"].fillna("⚠️ Sin datos recientes")
    else:
        df["estado"] = "⚠️ Sin datos"

    # Calcular costo por metro basado en metros_totales
    if "precio" in df.columns and "metros_totales" in df.columns:
        df["$/Metro"] = df["precio"] / df["metros_totales"]
    else:
        df["$/Metro"] = 0

    # Calcular porcentaje de descuento si existe precio normal
    if "precio" in df.columns and "precio_normal" in df.columns:
        df["Descuento"] = df.apply(
            lambda row: f"-{int(100 - (row['precio'] / row['precio_normal'] * 100))}%" 
            if pd.notna(row['precio_normal']) and pd.notna(row['precio']) and row['precio_normal'] > row['precio'] 
            else "—", 
            axis=1
        )
    else:
        df["Descuento"] = "—"

    skus_totales = len(df)
    skus_ovella = len(df[df['marca'].str.lower() == 'ovella']) if 'marca' in df.columns else 0
    errores = len(df[df['estado'].str.contains("⚠️|Últ|Error|Sin", na=False)])
    
    # Métricas superiores
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("SKU de Ovella", skus_ovella)
    col2.metric("SKU Monitoreados", skus_totales)
    col3.metric("Disponibles", skus_totales - errores)
    col4.metric("Con Advertencia / Error", errores)
    
    st.write("") 

    # Mapeo de columnas para la vista final
    columnas_mostrar = {
        "retailer": "Retailer",
        "marca": "Marca",
        "producto": "Producto",
        "metros_totales": "Metros Totales",
        "precio_normal": "Precio Lista",
        "precio": "Precio Oferta",
        "Descuento": "Promoción",
        "$/Metro": "$/Metro",
        "estado": "Estado"
    }
    
    cols_existentes = [c for c in columnas_mostrar.keys() if c in df.columns]
    df_vista = df[cols_existentes].rename(columns=columnas_mostrar)
    
    if "Retailer" in df_vista.columns: df_vista["Retailer"] = df_vista["Retailer"].str.title()
    if "Marca" in df_vista.columns: df_vista["Marca"] = df_vista["Marca"].str.title()

    # Función estándar para renderizar la tabla con barras de progreso
    def renderizar_tabla(dataframe_a_mostrar):
        st.dataframe(
            dataframe_a_mostrar,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Metros Totales": st.column_config.NumberColumn("Metros Totales", format="%d m"),
                "Precio Lista": st.column_config.NumberColumn("Precio Lista", format="$%d"),
                "Precio Oferta": st.column_config.NumberColumn("Precio Oferta", format="$%d"),
                "$/Metro": st.column_config.ProgressColumn(
                    "$/Metro",
                    help="Costo por metro. Barra más llena = más caro.",
                    format="$%.1f",
                    min_value=int(df_vista["$/Metro"].min()) if not df_vista.empty and df_vista["$/Metro"].min() > 0 else 10,
                    max_value=int(df_vista["$/Metro"].max()) if not df_vista.empty else 40
                ),
                "Estado": st.column_config.TextColumn("Estado")
            }
        )

    # 4. PESTAÑAS BASADAS EN LOS METROS TOTALES DE OVELLA
    st.markdown("### Detalle por Formato y Metraje")
    
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 Todos los SKUs", 
        "🧻 200 m (50m x 4)", 
        "🧻 120 m (30m x 4)", 
        "🧻 88 m (22m x 4)", 
        "🧻 132 m (22m x 6)"
    ])

    with tab1:
        st.subheader("Consolidado General de Mercado")
        renderizar_tabla(df_vista)

    with tab2:
        st.subheader("Formato: 200 Metros Totales (Equivalente Ovella 50m 4un)")
        df_f1 = df[df["metros_totales"] == 200]
        if not df_f1.empty:
            cols_f = [c for c in columnas_mostrar.keys() if c in df_f1.columns]
            renderizar_tabla(df_f1[cols_f].rename(columns=columnas_mostrar))
        else:
            st.info("No hay productos asociados a este metraje.")

    with tab3:
        st.subheader("Formato: 120 Metros Totales (Equivalente Ovella 30m 4un)")
        df_f2 = df[df["metros_totales"] == 120]
        if not df_f2.empty:
            cols_f = [c for c in columnas_mostrar.keys() if c in df_f2.columns]
            renderizar_tabla(df_f2[cols_f].rename(columns=columnas_mostrar))
        else:
            st.info("No hay productos asociados a este metraje.")

    with tab4:
        st.subheader("Formato: 88 Metros Totales (Equivalente Ovella 22m 4un)")
        df_f3 = df[df["metros_totales"] == 88]
        if not df_f3.empty:
            cols_f = [c for c in columnas_mostrar.keys() if c in df_f3.columns]
            renderizar_tabla(df_f3[cols_f].rename(columns=columnas_mostrar))
        else:
            st.info("No hay productos asociados a este metraje.")

    with tab5:
        st.subheader("Formato: 132 Metros Totales (Equivalente Ovella 22m 6un)")
        df_f4 = df[df["metros_totales"] == 132]
        if not df_f4.empty:
            cols_f = [c for c in columnas_mostrar.keys() if c in df_f4.columns]
            renderizar_tabla(df_f4[cols_f].rename(columns=columnas_mostrar))
        else:
            st.info("No hay productos asociados a este metraje.")

else:
    st.warning("No se encontraron datos procesados. Por favor, revisa que el archivo datos_procesados.json exista.")
