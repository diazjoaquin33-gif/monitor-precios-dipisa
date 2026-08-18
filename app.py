import streamlit as st
import json
import requests
from google import genai

# Configuración visual de la aplicación
st.set_page_config(
    page_title="Dipisa & Ovella - Monitor de Pricing",
    page_icon="📊",
    layout="wide"
)

# Clave de Gemini desde los Secrets de Streamlit
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "")

# Lista de productos de la categoría a monitorear
PRODUCTOS = [
    {
        "marca": "Noble",
        "sku_nombre": "Doble Hoja 23m 4 un",
        "retailer": "Santa Isabel",
        "metros_totales": 92,
        "sku_id": "1859328",
        "url": "https://www.santaisabel.cl/papel-higienico-doble-hoja-23-m-4-un-1859328/p"
    },
    {
        "marca": "Noble",
        "sku_nombre": "Doble Hoja 22m 40 un",
        "retailer": "Santa Isabel",
        "metros_totales": 880,
        "sku_id": "1960588",
        "url": "https://www.santaisabel.cl/papel-higienico-noble-doble-hoja-22-m-40-un-1960588/p"
    },
    {
        "marca": "Confort",
        "sku_nombre": "Doble Hoja 22m 40 un",
        "retailer": "Santa Isabel",
        "metros_totales": 880,
        "sku_id": "1997284",
        "url": "https://www.santaisabel.cl/papel-higienico-confort-doble-hoja-22-m-40-un-1997284/p"
    }
]

def extraer_precios_api():
    """Consulta la API de checkout de VTEX/Santa Isabel para obtener precios oficiales y stock."""
    resultados = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Origin": "https://www.santaisabel.cl",
        "Referer": "https://www.santaisabel.cl/"
    }
    
    for prod in PRODUCTOS:
        precio_normal = None
        precio_oferta = None
        stock = True
        
        # 1. Consulta al endpoint de simulación de compra VTEX
        try:
            order_url = "https://www.santaisabel.cl/api/checkout/pub/orderforms/simulation"
            payload = {
                "items": [{"id": prod["sku_id"], "quantity": 1, "seller": "1"}],
                "country": "CHL"
            }
            res = requests.post(order_url, json=payload, headers=headers, timeout=8)
            if res.status_code == 200:
                data = res.json()
                if "items" in data and len(data["items"]) > 0:
                    item_data = data["items"][0]
                    precio_oferta = int(item_data.get("price", 0) / 100)
                    precio_normal = int(item_data.get("listPrice", precio_oferta) / 100)
                    stock = item_data.get("availability") == "available"
        except Exception:
            pass

        # 2. Respaldo al catálogo público si el primero falla
        if not precio_oferta or precio_oferta == 0:
            try:
                cat_url = f"https://www.santaisabel.cl/api/catalog_system/pub/products/search?fq=skuId:{prod['sku_id']}"
                res = requests.get(cat_url, headers=headers, timeout=8)
                if res.status_code == 200:
                    data = res.json()
                    if data and len(data) > 0:
                        offer = data[0]["items"][0]["sellers"][0]["commertialOffer"]
                        precio_oferta = int(offer.get("Price", 0))
                        precio_normal = int(offer.get("ListPrice", precio_oferta))
                        stock = offer.get("AvailableQuantity", 0) > 0
            except Exception:
                pass

        # Procesamiento y cálculo de $/metro
        if precio_oferta and precio_oferta > 0:
            precio_metro = round(precio_oferta / prod["metros_totales"], 1)
            resultados.append({
                "marca": prod["marca"],
                "sku": prod["sku_nombre"],
                "retailer": prod["retailer"],
                "metros_totales": prod["metros_totales"],
                "precio_normal": f"${precio_normal:,.0f}".replace(",", "."),
                "precio_oferta": f"${precio_oferta:,.0f}".replace(",", "."),
                "precio_metro": f"${precio_metro} /m",
                "precio_metro_num": precio_metro,
                "estado": "En Oferta" if precio_oferta < precio_normal else ("Disponible" if stock else "Sin Stock"),
                "url": prod["url"]
            })
        else:
            resultados.append({
                "marca": prod["marca"],
                "sku": prod["sku_nombre"],
                "retailer": prod["retailer"],
                "metros_totales": prod["metros_totales"],
                "precio_normal": "N/D",
                "precio_oferta": "N/D",
                "precio_metro": "N/D",
                "precio_metro_num": 0,
                "estado": "Error de Conexión",
                "url": prod["url"]
            })
            
    return resultados

def procesar_ia(datos):
    """Genera el resumen y recomendaciones comerciales con Gemini."""
    if not GEMINI_API_KEY:
        return {
            "resumen": "Se obtuvieron los precios correctamente. Para ver el análisis cualitativo con IA, añade tu GEMINI_API_KEY en los Secrets de Streamlit.",
            "estrategia": "Añade GEMINI_API_KEY en Settings > Secrets de la aplicación.",
            "alertas": ["Falta configurar la clave de Gemini en la plataforma."]
        }

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
    Eres un analista de pricing senior para Dipisa y su marca Ovella.
    A continuación tienes la lista de precios reales extraídos en vivo desde los retailers:
    {json.dumps(datos, indent=2, ensure_ascii=False)}

    Con base en estos números reales:
    1. Genera un resumen comercial ejecutivo de 2 líneas con la conclusión principal para Dipisa.
    2. Define la recomendación estratégica puntual para Ovella (precio objetivo por metro para ser competitivo frente a Noble y Confort).
    3. Formula 2 alertas comerciales clave (quiebres de stock, ofertas agresivas o paridad).

    Devuelve ÚNICAMENTE un JSON válido con esta estructura exacta:
    {{
      "resumen": "texto",
      "estrategia": "texto",
      "alertas": ["alerta 1", "alerta 2"]
    }}
    """
    
    try:
        res = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )
        texto = res.text.strip()
        if texto.startswith("```json"): texto = texto[7:]
        if texto.startswith("```"): texto = texto[3:]
        if texto.endswith("```"): texto = texto[:-3]
        return json.loads(texto.strip())
    except Exception as e:
        return {
            "resumen": "Precios obtenidos con éxito desde la API de los retailers.",
            "estrategia": "Comparar directamente los $/metro en la tabla inferior.",
            "alertas": [f"Nota de IA: {e}"]
        }

# --- Interfaz de Usuario ---
st.title("📊 Dipisa & Ovella — Monitor de Pricing en Vivo")
st.caption("Precios actualizados en tiempo real para análisis comercial y benchmarking de retail")

if st.button("🚀 Actualizar Precios Ahora", type="primary"):
    with st.spinner("Consultando precios oficiales y generando análisis con IA..."):
        datos_tabla = extraer_precios_api()
        analisis = procesar_ia(datos_tabla)

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
    
    columnas_mostrar = [
        {
            "Retailer": d["retailer"], 
            "Marca": d["marca"], 
            "SKU": d["sku"], 
            "Metros": f"{d['metros_totales']} m", 
            "Precio Normal": d["precio_normal"], 
            "Precio Oferta": d["precio_oferta"], 
            "$/Metro": d["precio_metro"], 
            "Estado": d["estado"]
        }
        for d in datos_tabla
    ]
    st.dataframe(columnas_mostrar, use_container_width=True)
else:
    st.info("Presiona el botón superior para consultar los precios actuales de la competencia.")
