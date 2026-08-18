import streamlit as st
import json
import urllib.parse
import requests

st.set_page_config(
    page_title="Dipisa & Ovella — Monitor de Pricing",
    page_icon="📊",
    layout="wide"
)

# SKUs y Product IDs exactos de Santa Isabel
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
        "url": "https://www.santaisabel.cl/ph-doble-hoja-noble-dh-40-rollos-1960588/p"
    },
    {
        "marca": "Confort",
        "sku_nombre": "Doble Hoja 22m 40 un",
        "retailer": "Santa Isabel",
        "metros_totales": 880,
        "sku_id": "1997284",
        "url": "https://www.santaisabel.cl/papel-higienico-confort-dh-22mt-40un-1997284/p"
    }
]

def obtener_datos_vtex(sku_id):
    """Consulta la API de catálogo de VTEX usando un puente proxy para evitar bloqueos de IP."""
    target_url = f"https://www.santaisabel.cl/api/catalog_system/pub/products/search?fq=skuId:{sku_id}"
    proxy_url = f"https://api.allorigins.win/get?url={urllib.parse.quote(target_url)}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    
    try:
        # Intento 1: Vía proxy
        r = requests.get(proxy_url, headers=headers, timeout=8)
        if r.status_code == 200:
            raw_text = r.json().get("contents", "")
            data = json.loads(raw_text)
            if data and len(data) > 0:
                offer = data[0]["items"][0]["sellers"][0]["commertialOffer"]
                precio_oferta = int(offer.get("Price", 0))
                precio_normal = int(offer.get("ListPrice", precio_oferta))
                return precio_oferta, precio_normal
    except Exception:
        pass

    try:
        # Intento 2: Directo
        r = requests.get(target_url, headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data and len(data) > 0:
                offer = data[0]["items"][0]["sellers"][0]["commertialOffer"]
                precio_oferta = int(offer.get("Price", 0))
                precio_normal = int(offer.get("ListPrice", precio_oferta))
                return precio_oferta, precio_normal
    except Exception:
        pass

    return None, None

@st.cache_data(ttl=1800)
def consultar_precios_en_vivo():
    resultados = []

    for prod in PRODUCTOS:
        precio_oferta, precio_normal = obtener_datos_vtex(prod["sku_id"])

        if precio_oferta and precio_oferta > 0:
            if not precio_normal or precio_normal < precio_oferta:
                precio_normal = precio_oferta

            precio_metro = round(precio_oferta / prod["metros_totales"], 1)
            descuento = round((1 - (precio_oferta / precio_normal)) * 100) if precio_normal > precio_oferta else 0

            if descuento > 0:
                estado = f"🔥 {descuento}% DCTO"
            else:
                estado = "Normal"

            resultados.append({
                "marca": prod["marca"],
                "sku": prod["sku_nombre"],
                "retailer": prod["retailer"],
                "metros_totales": prod["metros_totales"],
                "precio_normal": f"${precio_normal:,.0f}".replace(",", "."),
                "precio_oferta": f"${precio_oferta:,.0f}".replace(",", "."),
                "precio_metro": f"${precio_metro} /m",
                "precio_metro_num": precio_metro,
                "estado": estado,
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
                "estado": "Reintentar",
                "url": prod["url"]
            })

    return resultados

def calcular_resumen_comercial(datos):
    validos = [d for d in datos if d["precio_metro_num"] > 0]
    if not validos:
        return {
            "resumen": "No se pudieron obtener precios en este momento. Presiona el botón de recarga.",
            "estrategia": "Verificar conexión.",
            "alertas": ["Sin datos en vivo."]
        }

    mas_barato = min(validos, key=lambda x: x["precio_metro_num"])
    mas_caro = max(validos, key=lambda x: x["precio_metro_num"])
    promedio_m = round(sum(d["precio_metro_num"] for d in validos) / len(validos), 1)

    resumen = (
        f"El precio promedio de mercado se sitúa en **${promedio_m}/m**. "
        f"La opción más económica por metro es **{mas_barato['marca']} ({mas_barato['sku']})** con **{mas_barato['precio_metro']}**."
    )

    precio_objetivo = round(mas_barato["precio_metro_num"] * 0.95, 1)
    estrategia = (
        f"Para que **Ovella** lidere en competitividad frente a la competencia, el precio objetivo sugerido debe ser igual o inferior a "
        f"**${precio_objetivo}/m** frente a los formatos familiares."
    )

    alertas = [
        f"📌 **Piso de Categoría:** {mas_barato['marca']} marca el precio mínimo en {mas_barato['precio_metro']}.",
        f"📈 **Diferencial de Formato:** El formato unitario ({mas_caro['marca']}) cuesta un {round(((mas_caro['precio_metro_num']/mas_barato['precio_metro_num'])-1)*100)}% más por metro que el pack ahorro."
    ]

    return {
        "resumen": resumen,
        "estrategia": estrategia,
        "alertas": alertas
    }

# --- Interfaz Gráfica ---
st.title("📊 Dipisa & Ovella — Monitor de Pricing en Vivo")
st.caption("Benchmarking en tiempo real • 100% Automático y autónomo")

datos_tabla = consultar_precios_en_vivo()
analisis = calcular_resumen_comercial(datos_tabla)

col1, col2 = st.columns([2, 1])
with col1:
    st.subheader("💡 Resumen Comercial")
    st.markdown(analisis["resumen"])
    st.info(f"🎯 **Estrategia Ovella:** {analisis['estrategia']}")

with col2:
    st.subheader("⚠️ Alertas del Mercado")
    for a in analisis["alertas"]:
        st.warning(a)

st.subheader("📋 Tabla Comparativa de Precios y $/Metro")

columnas_mostrar = [
    {
        "Retailer": d["retailer"], 
        "Marca": d["marca"], 
        "SKU": d["sku"], 
        "Metros": f"{d['metros_totales']} m", 
        "Precio Normal (Tachado)": d["precio_normal"], 
        "Precio Oferta (Cobro)": d["precio_oferta"], 
        "$/Metro": d["precio_metro"], 
        "Estado": d["estado"]
    }
    for d in datos_tabla
]

try:
    st.dataframe(columnas_mostrar, width="stretch")
except TypeError:
    st.dataframe(columnas_mostrar, use_container_width=True)

col_btn1, _ = st.columns([1, 4])
with col_btn1:
    if st.button("🔄 Forzar Recarga"):
        st.cache_data.clear()
        st.rerun()
