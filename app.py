import streamlit as st
import json
from google import genai
from playwright.sync_api import sync_playwright

# Configuración visual de la página
st.set_page_config(page_title="Dipisa & Ovella - Pricing", page_icon="📊", layout="wide")

# Clave de Gemini (en local o desde secrets de la nube)
# Obtiene la clave de forma segura desde Streamlit o variable de entorno
import os

# Lee la clave desde la configuración segura de Streamlit o del sistema
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))

# Lista de productos
PRODUCTOS = [
    {
        "marca": "Noble",
        "sku_nombre": "Doble Hoja 23m 4 un",
        "retailer": "Santa Isabel",
        "metros_totales": 92,
        "url": "https://www.santaisabel.cl/papel-higienico-doble-hoja-23-m-4-un-1859328/p"
    },
    {
        "marca": "Noble",
        "sku_nombre": "Doble Hoja 22m 40 un",
        "retailer": "Santa Isabel",
        "metros_totales": 880,
        "url": "https://www.santaisabel.cl/ph-doble-hoja-noble-dh-40-rollos-1960588/p"
    },
    {
        "marca": "Confort",
        "sku_nombre": "Doble Hoja 22m 40 un",
        "retailer": "Santa Isabel",
        "metros_totales": 880,
        "url": "https://www.santaisabel.cl/ph-doble-hoja-noble-dh-40-rollos-1960588/p"
    }
]

def extraer_precios():
    resultados = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for prod in PRODUCTOS:
            try:
                page.goto(prod["url"], timeout=45000)
                page.wait_for_timeout(3000)
                texto = page.inner_text("body")
                resultados.append({
                    "marca": prod["marca"],
                    "sku": prod["sku_nombre"],
                    "retailer": prod["retailer"],
                    "metros_totales": prod["metros_totales"],
                    "url": prod["url"],
                    "texto_capturado": texto[:2000]
                })
            except Exception:
                resultados.append({
                    "marca": prod["marca"],
                    "sku": prod["sku_nombre"],
                    "retailer": prod["retailer"],
                    "metros_totales": prod["metros_totales"],
                    "url": prod["url"],
                    "texto_capturado": "ERROR_O_SIN_STOCK"
                })
        browser.close()
    return resultados

def procesar_ia(datos):
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
    Analiza estos datos de e-commerce en vivo:
    {json.dumps(datos, indent=2, ensure_ascii=False)}

    Devuelve ÚNICAMENTE un JSON válido con esta estructura exacta:
    {{
      "resumen": "Resumen ejecutivo de 2 líneas para Dipisa/Ovella.",
      "estrategia": "Acción sugerida para Ovella.",
      "productos": [
        {{
          "Retailer": "Santa Isabel",
          "Marca": "Noble",
          "SKU": "Doble Hoja 23m 4 un",
          "Precio Normal": "$1.950",
          "Precio Oferta": "$1.950",
          "Metros": "92 m",
          "$/Metro": "$21,2 /m",
          "Estado": "Normal"
        }}
      ],
      "alertas": ["Alerta 1...", "Alerta 2..."]
    }}
    """
    res = client.models.generate_content(model="gemini-3.6-flash", contents=prompt)
    texto = res.text.strip()
    if texto.startswith("```json"): texto = texto[7:]
    if texto.startswith("```"): texto = texto[3:]
    if texto.endswith("```"): texto = texto[:-3]
    return json.loads(texto.strip())

# --- Interfaz Gráfica ---
st.title("📊 Dipisa & Ovella — Monitor de Pricing en Vivo")
st.caption("Consulta de precios en tiempo real para el equipo comercial")

if st.button("🚀 Actualizar Precios Ahora", type="primary"):
    with st.spinner("Consultando los supermercados en vivo y procesando con Gemini..."):
        datos = extraer_precios()
        analisis = procesar_ia(datos)

    st.success("¡Datos actualizados con éxito!")
    
    col1, col2 = st.columns([2, 1])
    with col1:
        st.subheader("💡 Resumen Comercial")
        st.write(analisis.get("resumen", ""))
        st.info(f"**Estrategia Ovella:** {analisis.get('estrategia', '')}")
    
    with col2:
        st.subheader("⚠️ Alertas")
        for a in analisis.get("alertas", []):
            st.warning(a)

    st.subheader("📋 Tabla de Precios y $/Metro")
    st.dataframe(analisis.get("productos", []), use_container_width=True)
else:
    st.info("Presiona el botón superior para consultar los precios actuales.")