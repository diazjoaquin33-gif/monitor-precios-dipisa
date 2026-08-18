import streamlit as st
import requests

st.set_page_config(
    page_title="Dipisa & Ovella - Pricing",
    page_icon="📊",
    layout="wide"
)

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

@st.cache_data(ttl=1800)  # Guarda los precios 30 minutos para que la web vuele
def extraer_precios_api():
    resultados = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }
    
    for prod in PRODUCTOS:
        precio_normal = None
        precio_oferta = None
        stock = True
        
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
    return resultados

def generar_analisis_automatico(datos):
    """Genera métricas comerciales sin usar tokens."""
    validos = [d for d in datos if d["precio_metro_num"] > 0]
    if not validos:
        return {
            "resumen": "No se pudieron obtener datos de precios en este momento.",
            "estrategia": "Revisar disponibilidad en el e-commerce.",
            "alertas": ["Error al consultar catálogo."]
        }
    
    mas_barato = min(validos, key=lambda x: x["precio_metro_num"])
    mas_caro = max(validos, key=lambda x: x["precio_metro_num"])
    promedio_m = round(sum(d["precio_metro_num"] for d in validos) / len(validos), 1)

    resumen = (
        f"El mercado presenta un precio promedio de **${promedio_m}/m**. "
        f"La opción más económica por metro es **{mas_barato['marca']} ({mas_barato['sku']})** a **{mas_barato['precio_metro']}**."
    )
    
    precio_objetivo = round(mas_barato["precio_metro_num"] * 0.95, 1)
    estrategia = (
        f"Para que Ovella lidere en competitividad por volumen, el precio objetivo sugerido debe ser inferior a "
        f"**${precio_objetivo}/m** frente a los formatos familiares de 880 metros."
    )
    
    alertas = [
        f"📌 **Benchmark Mínimo:** {mas_barato['marca']} fija el piso de la categoría en {mas_barato['precio_metro']}.",
        f"📈 **Diferencial de Formato:** El SKU de menor metraje ({mas_caro['sku']}) es un {round(((mas_caro['precio_metro_num']/mas_barato['precio_metro_num'])-1)*100)}% más caro por metro que el pack ahorro."
    ]

    return {
        "resumen": resumen,
        "estrategia": estrategia,
        "alertas": alertas
    }

# --- Interfaz Gráfica ---
st.title("📊 Dipisa & Ovella — Monitor de Pricing en Vivo")
st.caption("Benchmarking en tiempo real • Actualización automática ilimitada")

datos_tabla = extraer_precios_api()
analisis = generar_analisis_automatico(datos_tabla)

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
        "Precio Normal": d["precio_normal"], 
        "Precio Oferta": d["precio_oferta"], 
        "$/Metro": d["precio_metro"], 
        "Estado": d["estado"]
    }
    for d in datos_tabla
]
st.dataframe(columnas_mostrar, use_container_width=True)

if st.button("🔄 Forzar Recarga de Precios"):
    st.cache_data.clear()
    st.rerun()
