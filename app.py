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
        # Cargar catálogo completo ignorando los marcados con #
        df_prod = pd.read_csv(PRODUCTOS_PATH, comment="#")
        # Cargar precios procesados por el bot
        with open(DATOS_PATH, "r", encoding="utf-8") as f:
            precios = json.load(f)
        df_precios = pd.DataFrame(precios)
        
        # Unir ambas tablas por el SKU interno (Left join para no perder ningún SKU del CSV)
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
    # 3. MÉTRICAS EJECUTIVAS SUPERIORES
    skus_totales = len(df)
    skus_ovella = len(df[df['marca'].str.lower() == 'ovella']) if 'marca' in df.columns else 0
    
    # Manejo seguro de estados nulos o vacíos
    if "estado" in df.columns:
        df["estado"] = df["estado"].fillna("⚠️ Sin datos recientes")
    else:
        df["estado"] = "⚠️ Sin datos"

    errores = len(df[df['estado'].str.contains("⚠️|Últ|Error|Sin", na=False)])
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("SKU de Ovella", skus_ovella)
    col2.metric("SKU Monitoreados", skus_totales)
    col3.metric("Disponibles", skus_totales - errores)
    col4.metric("Con Advertencia / Error", errores)
    
    st.write("") 

    # 4. PREPARACIÓN DE COLUMNAS (Cálculos de metro y descuento)
    if "precio" in df.columns and "metros_totales" in df.columns:
        df["$/Metro"] = df["precio"] / df["metros_totales"]
    else:
        df["$/Metro"] = 0

    # Calcular porcentaje de descuento
    if "precio" in df.columns and "precio_normal" in df.columns:
        df["Descuento"] = df.apply(
            lambda row: f"-{int(100 - (row['precio'] / row['precio_normal'] * 100))}%" 
            if pd.notna(row['precio_normal']) and pd.notna(row['precio']) and row['precio_normal'] > row['precio'] 
            else "—", 
            axis=1
        )
    else:
        df["Descuento"] = "—"

    # Mapeo final de nombres para la tabla
    columnas_mostrar = {
        "retailer": "Retailer",
        "marca": "Marca",
        "producto": "Producto",
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

    # 5. ORGANIZACIÓN POR PESTAÑAS (Muestra todo el catálogo o agrupa sin perder SKUs)
    st.markdown("### Detalle General del Portafolio")
    
    tab1, tab2, tab3 = st.tabs(["📊 Todos los SKUs Analizados", "🧻 Papel Higiénico", "🧹 Toallas / Otros"])

    def renderizar_tabla(dataframe_a_mostrar):
        """Renderiza la tabla de manera segura y limpia"""
        st.dataframe(
            dataframe_a_mostrar,
            use_container_width=True,
            hide_index=True,
            column_config={
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

    with tab1:
        st.subheader("Vista Consolidada de Mercado")
        # Aquí se muestran TODOS los SKUs sin excepción para que no falte ninguno
        renderizar_tabla(df_vista)

    with tab2:
        st.subheader("Filtro: Papel Higiénico")
        if "producto" in df.columns:
            df_papel = df[~df["producto"].str.contains("Toalla", case=False, na=False)]
            cols_p = [c for c in columnas_mostrar.keys() if c in df_papel.columns]
            vista_p = df_papel[cols_p].rename(columns=columnas_mostrar)
            if "Retailer" in vista_p.columns: vista_p["Retailer"] = vista_p["Retailer"].str.title()
            if "Marca" in vista_p.columns: vista_p["Marca"] = vista_p["Marca"].str.title()
            renderizar_tabla(vista_p)
        else:
            renderizar_tabla(df_vista)

    with tab3:
        st.subheader("Filtro: Toallas de Papel y Otros")
        if "producto" in df.columns:
            df_toalla = df[df["producto"].str.contains("Toalla", case=False, na=False)]
            if not df_toalla.empty:
                cols_t = [c for c in columnas_mostrar.keys() if c in df_toalla.columns]
                vista_t = df_toalla[cols_t].rename(columns=columnas_mostrar)
                if "Retailer" in vista_t.columns: vista_t["Retailer"] = vista_t["Retailer"].str.title()
                if "Marca" in vista_t.columns: vista_t["Marca"] = vista_t["Marca"].str.title()
                renderizar_tabla(vista_t)
            else:
                st.info("No hay productos de toallas de papel registrados con esa etiqueta.")

else:
    st.warning("No se encontraron datos procesados. Por favor, revisa que el archivo datos_procesados.json exista.")
