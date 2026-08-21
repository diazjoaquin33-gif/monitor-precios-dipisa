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
        # Cargar catálogo ignorando los marcados con #
        df_prod = pd.read_csv(PRODUCTOS_PATH, comment="#")
        # Cargar precios procesados por el bot
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
    # Contar cuántos tienen estado de alerta o error
    errores = len(df[df['estado'].str.contains("⚠️|Últ|Error", na=False)]) if 'estado' in df.columns else 0
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("SKU de Ovella", skus_ovella)
    col2.metric("SKU Monitoreados", skus_totales)
    col3.metric("Disponibles", skus_totales - errores)
    col4.metric("Con Advertencia / Respaldo", errores)
    
    st.write("") 

    # 4. PREPARACIÓN DE COLUMNAS (Cálculos de metro y descuento)
    if "precio" in df.columns and "metros_totales" in df.columns:
        df["$/Metro"] = df["precio"] / df["metros_totales"]
    else:
        df["$/Metro"] = 0

    # Calcular porcentaje de descuento si hay precio normal y oferta
    if "precio" in df.columns and "precio_normal" in df.columns:
        df["Descuento"] = df.apply(
            lambda row: f"-{int(100 - (row['precio'] / row['precio_normal'] * 100))}%" 
            if pd.notna(row['precio_normal']) and pd.notna(row['precio']) and row['precio_normal'] > row['precio'] 
            else "—", 
            axis=1
        )
    else:
        df["Descuento"] = "—"

    # Recuperar el estado original que venía del JSON (En vivo / Último precio)
    # Si el bot actualizó hoy, ponemos "En vivo", si usó respaldo, mantenemos el texto del JSON
    if "estado" not in df.columns:
        df["estado"] = "Disponible"

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

    # 5. ORGANIZACIÓN POR PESTAÑAS (Filtrando por formato real si existe la columna en el CSV)
    st.markdown("### Detalle por Producto y Formato")
    
    # Creamos pestañas basadas en los formatos reales que maneja tu portafolio
    tab1, tab2, tab3 = st.tabs(["🧻 Doble Hoja 50m", "🧻 Doble Hoja 30m / Otros", "🧻 Toallas de Papel"])

    def renderizar_tabla_filtrada(filtro_texto):
        """Función auxiliar para filtrar el DataFrame según el formato"""
        if "producto" in df.columns:
            # Filtramos el dataframe original usando la palabra clave
            df_filtrado = df[df["producto"].str.contains(filtro_texto, case=False, na=False)]
            if not df_filtrado.empty:
                # Reconstruimos la vista para el subgrupo
                cols_f = [c for c in columnas_mostrar.keys() if c in df_filtrado.columns]
                vista_f = df_filtrado[cols_f].rename(columns=columnas_mostrar)
                if "Retailer" in vista_f.columns: vista_f["Retailer"] = vista_f["Retailer"].str.title()
                if "Marca" in vista_f.columns: vista_f["Marca"] = vista_f["Marca"].str.title()
                
                st.dataframe(
                    vista_f,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Precio Lista": st.column_config.NumberColumn("Precio Lista", format="$%d"),
                        "Precio Oferta": st.column_config.NumberColumn("Precio Oferta", format="$%d"),
                        "$/Metro": st.column_config.ProgressColumn(
                            "$/Metro",
                            help="Costo por metro. Barra más llena = más caro.",
                            format="$%.1f",
                            min_value=int(df_vista["$/Metro"].min()) if not df_vista.empty else 10,
                            max_value=int(df_vista["$/Metro"].max()) if not df_vista.empty else 40
                        ),
                        "Estado": st.column_config.TextColumn("Estado")
                    }
                )
                return
        st.info(f"No hay productos específicos registrados para el filtro: {filtro_texto}")

    with tab1:
        st.subheader("Formato 50 metros (Ej: Doble Hoja 50m 4un)")
        renderizar_tabla_filtrada("50 m")

    with tab2:
        st.subheader("Otros Formatos / Metrajes")
        renderizar_tabla_filtrada("30 m") # O el texto que identifique a los otros formatos

    with tab3:
        st.subheader("Toallas de Papel")
        renderizar_tabla_filtrada("Toalla")

else:
    st.warning("No se encontraron datos procesados. Por favor, revisa que el archivo datos_procesados.json exista.")
