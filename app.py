import streamlit as st
import pandas as pd
import json
from pathlib import Path

# 1. CONFIGURACIÓN DE LA PÁGINA (Debe ser la primera línea)
st.set_page_config(
    page_title="Monitor de Precios | Ovella",
    page_icon="🧻",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 2. OCULTAR RASTROS DE STREAMLIT (Marca blanca para Dipisa)
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    /* Resaltar filas de Ovella en la tabla */
    .ovella-row { background-color: #e6f2ff !important; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

# 3. ENCABEZADO
st.title("📊 Monitor Competitivo de Precios - Dipisa")
st.markdown("**Inteligencia de mercado:** Pricing de Ovella vs. Competencia en Retail")
st.markdown("---")

# 4. CARGA DE DATOS
BASE_DIR = Path(__file__).parent

@st.cache_data(ttl=600) # Recarga cada 10 mins
def cargar_datos():
    # Cargar JSON
    with open(BASE_DIR / 'datos_procesados.json', 'r', encoding='utf-8') as f:
        datos = json.load(f)
    df_json = pd.DataFrame(datos)
    
    # Cargar CSV
    df_csv = pd.read_csv(BASE_DIR / 'productos.csv', comment='#', skip_blank_lines=True)
    
    # Cruzar datos (Para tener el nombre del producto y la marca)
    df = pd.merge(df_json, df_csv, on='sku_interno', how='inner')
    
    # Calcular descuentos reales
    df['precio'] = pd.to_numeric(df['precio'], errors='coerce')
    df['precio_normal'] = pd.to_numeric(df['precio_normal'], errors='coerce')
    df['% Dcto'] = (100 - (df['precio'] / df['precio_normal'] * 100)).fillna(0).round(1)
    
    # Formatear columnas para visualización
    df = df[['retailer', 'sku_interno', 'producto', 'precio', 'precio_normal', '% Dcto', 'estado', 'fecha_act']]
    df.columns = ['Supermercado', 'SKU', 'Producto', 'Precio Oferta', 'Precio Lista', '% Dcto', 'Estado', 'Última Act.']
    
    return df

try:
    df = cargar_datos()
    
    # 5. TARJETAS DE KPIs (Resumen gerencial)
    st.subheader("💡 Resumen del Mercado")
    col1, col2, col3, col4 = st.columns(4)
    
    total_skus = len(df)
    en_oferta = len(df[df['% Dcto'] > 0])
    
    # Identificar si hay descuentos fuertes (más del 20%)
    ofertas_agresivas = len(df[df['% Dcto'] >= 20])

    col1.metric("SKUs Monitoreados", f"{total_skus} prod.")
    col2.metric("Productos en Oferta", f"{en_oferta} prod.", f"{(en_oferta/total_skus*100):.0f}% del catálogo")
    col3.metric("Ofertas Agresivas (>20%)", f"{ofertas_agresivas} prod.", "Alerta de competencia", delta_color="inverse")
    
    st.markdown("---")
    
    # 6. TABLA DE DATOS ESTILIZADA
    st.subheader("📋 Detalle de Precios por Producto")
    
    # Función para pintar de azul los productos de Ovella y de verde los descuentos
    def estilizar_tabla(row):
        estilos = [''] * len(row)
        # Si el producto dice "Ovella", pintamos la fila de un azul muy suave
        if 'ovella' in str(row['Producto']).lower():
            estilos = ['background-color: #E6F0FA; color: #004B87; font-weight: bold'] * len(row)
        
        # Destacar la columna de descuento si es mayor a 0
        idx_dcto = row.index.get_loc('% Dcto')
        if row['% Dcto'] > 0:
            estilos[idx_dcto] = 'color: #10B981; font-weight: bold;' # Verde para ofertas
            
        return estilos

    # Aplicar el estilo y formatear los números como pesos chilenos ($)
    st.dataframe(
        df.style.apply(estilizar_tabla, axis=1)\
                .format({"Precio Oferta": "${:,.0f}", "Precio Lista": "${:,.0f}", "% Dcto": "{:.1f}%"}),
        use_container_width=True,
        hide_index=True,
        height=600
    )

except Exception as e:
    st.error(f"Aún no hay datos procesados o hubo un error al cargar. Ejecuta el Scraper en Actions. Error: {e}")
