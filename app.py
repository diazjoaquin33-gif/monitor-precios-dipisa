import streamlit as st
import pandas as pd
import json
from pathlib import Path

# 1. CONFIGURACIÓN DE PÁGINA (Debe ser la primera línea)
st.set_page_config(page_title="Monitor Pricing | Dipisa", layout="wide", page_icon="📊")

# --- ZONA DE CARGA DE DATOS ---
# (Asumo la ruta de tus archivos basada en tu scraper)
BASE_DIR = Path(__file__).parent
DATOS_PATH = BASE_DIR / "datos_procesados.json"
PRODUCTOS_PATH = BASE_DIR / "productos.csv"

@st.cache_data(ttl=60) # Refresca los datos cada 1 minuto si hay cambios
def cargar_datos():
    try:
        # Cargar catálogo ignorando los que tienen #
        df_prod = pd.read_csv(PRODUCTOS_PATH, comment="#")
        # Cargar precios del bot
        with open(DATOS_PATH, "r", encoding="utf-8") as f:
            precios = json.load(f)
        df_precios = pd.DataFrame(precios)
        
        # Unir ambas tablas por el SKU interno
        if not df_precios.empty and not df_prod.empty:
            df = pd.merge(df_prod, df_precios, on="sku_interno", how="left")
            return df
    except Exception as e:
        st.error(f"Error cargando datos: {e}")
    return pd.DataFrame()

df = cargar_datos()

# 2. CABECERA CORPORATIVA
col_logo, col_titulo = st.columns([1, 4])
with col_logo:
    try:
        st.image("logo.png", width=180) 
    except:
        st.warning("⚠️ Falta subir logo.png a GitHub")
with col_titulo:
    st.title("Monitor de Pricing en Vivo")
    st.markdown("##### Inteligencia Competitiva Comercial — Dipisa & Ovella")

# Muestra la fecha de última actualización si hay datos
if not df.empty and "fecha_act" in df.columns:
    ultima_act = df["fecha_act"].dropna().iloc[0] if not df["fecha_act"].dropna().empty else "Desconocida"
    st.info(f"🕒 **Último informe generado por el bot:** {ultima_act}")
else:
    st.info("🕒 Esperando datos del bot...")

st.divider()

if not df.empty:
    # 3. MÉTRICAS EJECUTIVAS SUPERIORES
    skus_totales = len(df)
    skus_ovella = len(df[df['marca'].str.lower() == 'ovella']) if 'marca' in df.columns else 0
    errores = len(df[df['estado'].str.contains("⚠️|❌|Error", na=False)]) if 'estado' in df.columns else 0
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("SKU de Ovella", skus_ovella)
    col2.metric("SKU Monitoreados", skus_totales)
    col3.metric("Sin stock detectado", "0") # Aquí puedes conectar tu lógica de stock real
    col4.metric("Sin Conexión / Error", errores)
    
    st.write("") # Espacio
    st.write("") # Espacio

    # 4. PREPARACIÓN DE LA TABLA PARA EL DASHBOARD
    # Ajusta los nombres de las columnas a como los tenías en tu imagen
    if "precio" in df.columns and "metros_totales" in df.columns:
        df["$/Metro"] = df["precio"] / df["metros_totales"]
    
    # Renombrar columnas para que se vean bonitas en la tabla
    columnas_mostrar = {
        "retailer": "Retailer",
        "marca": "Marca",
        "producto": "Producto",
        "precio_normal": "Precio Lista",
        "precio": "Precio Oferta",
        "$/Metro": "$/Metro",
        "estado": "Estado"
    }
    
    # Filtrar solo las columnas que queremos mostrar y que existen en el df
    cols_existentes = [c for c in columnas_mostrar.keys() if c in df.columns]
    df_vista = df[cols_existentes].rename(columns=columnas_mostrar)
    
    # Poner la primera letra en mayúscula para Retailer y Marca
    if "Retailer" in df_vista.columns: df_vista["Retailer"] = df_vista["Retailer"].str.title()
    if "Marca" in df_vista.columns: df_vista["Marca"] = df_vista["Marca"].str.title()

    # 5. ORGANIZACIÓN POR PESTAÑAS (TABS)
    st.markdown("### Detalle por Producto y Formato")
    tab1, tab2, tab3 = st.tabs(["🧻 Doble Hoja 50m", "🧻 Doble Hoja 22m - 30m", "🧻 Toallas de Papel"])

    with tab1:
        st.subheader("Ovella — Doble Hoja 50 m 4 un (200 m totales)")
        
        # Simulación de métricas de la pestaña (conecta esto a tus variables reales)
        subcol1, subcol2, _ = st.columns([1, 1, 2])
        if "$/Metro" in df_vista.columns:
            min_metro = df_vista["$/Metro"].min()
            prom_metro = df_vista["$/Metro"].mean()
            subcol1.metric("Más barato ($/m)", f"${min_metro:.1f}" if pd.notna(min_metro) else "-")
            subcol2.metric("Promedio ($/m)", f"${prom_metro:.1f}" if pd.notna(prom_metro) else "-")
        
        # 6. TABLA INTERACTIVA CON BARRA DE PROGRESO
        st.dataframe(
            df_vista,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Precio Lista": st.column_config.NumberColumn("Precio Lista", format="$%d"),
                "Precio Oferta": st.column_config.NumberColumn("Precio Oferta", format="$%d"),
                "$/Metro": st.column_config.ProgressColumn(
                    "$/Metro",
                    help="Costo por metro cuadrado. Barra más llena = más caro.",
                    format="$%.1f",
                    min_value=10, # Configura el mínimo realista de tu mercado
                    max_value=30  # Configura el máximo realista de tu mercado
                ),
                "Estado": st.column_config.TextColumn("Estado")
            }
        )
        st.caption("💡 Haz clic en el encabezado '$/Metro' o 'Precio Oferta' para ordenar la tabla de menor a mayor.")

    with tab2:
        st.info("Aquí puedes filtrar tu DataFrame para mostrar los formatos más pequeños.")
        # Ejemplo: st.dataframe(df_vista[df_vista['Producto'].str.contains('22 m|30 m')])

    with tab3:
        st.info("Aquí puedes filtrar tu DataFrame para mostrar las toallas Nova / Scott / Favorita.")

else:
    st.warning("No se encontraron datos procesados. Por favor, revisa que el archivo datos_procesados.json exista y tenga información.")
