import streamlit as st
import requests

st.set_page_config(
    page_title="Dipisa & Ovella — Monitor de Pricing",
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

def obtener_precios_simulacion(session, sku_ids):
    """Consulta la API de simulación de orden VTEX para obtener precios de lista y oferta de todos los SKUs."""
    url = "https://www.santaisabel.cl/api/checkout/pub/orderforms/simulation?sc=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.santaisabel.cl",
        "Referer": "https://www.santaisabel.cl/"
    }
    
    payload = {
        "items": [{"id": str(sku_id), "quantity": 1, "seller": "1"} for sku_id in sku_ids],
        "country": "CHL"
    }
    
    precios_map = {}
    try:
        res = session.post(url, json=payload, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            for item in data.get("items", []):
                item_id = str(item.get("id"))
                # Los precios de VTEX simulation vienen en centavos (ej: 1077300 = $10.773)
                p_cobro = int(item.get("price", 0) / 100)
                p_lista = int(item.get("listPrice", p_cobro) / 100)
                precios_map[item_id] = {
                    "precio_oferta": p_cobro,
                    "precio_normal": p_lista if p_lista > 0 else p_cobro,
                    "stock": item.get("availability") == "available"
                }
    except Exception:
        pass
        
    return precios_map

def obtener_precios_catalogo_fallback(session, sku_id):
    """Método secundario por endpoint de catálogo público si la simulación falla."""
    url = f"https://www.santaisabel.cl/api/catalog_system/pub/products/search?fq=skuId:{sku_id}&sc=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }
    try:
        res = session.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            data = res.json()
            if data and len(data) > 0:
                offer = data[0]["items"][0]["sellers"][0]["commertialOffer"]
                p_price = int(offer.get("Price", 0))
                p_list = int(offer.get("ListPrice", p_price))
                p_spot = int(offer.get("spotPrice", p_price))
                
                p_oferta = p_spot if p_spot > 0 else p_price
                p_normal = p_list if p_list > 0 else p_oferta
                return {
                    "precio_oferta": p_oferta,
                    "precio_normal": p_normal,
                    "stock": offer.get("AvailableQuantity", 0) > 0
                }
    except Exception:
        pass
    return None

@st.cache_data(ttl=1800)
def consultar_precios_en_vivo():
    resultados = []
    session = requests.Session()
    
    # Obtener cookie de sesión base
    try:
        session.get("https://www.santaisabel.cl/", timeout=5)
    except Exception:
        pass

    sku_ids = [p["sku_id"] for p in PRODUCTOS]
    datos_simulacion = obtener_precios_simulacion(session, sku_ids)

    for prod in PRODUCTOS:
        sku_id = prod["sku_id"]
        info_precio = datos_simulacion.get(sku_id)

        # Si no vino en la simulación por lote, intentar consulta individual
        if not info_precio or info_precio.get("precio_oferta", 0) == 0:
            info_precio = obtener_precios_catalogo_fallback(session, sku_id)

        if info_precio and info_precio.get("precio_oferta", 0) > 0:
            precio_oferta = info_precio["precio_oferta"]
            precio_normal = info_precio["precio_normal"]
            
            if precio_normal < precio_oferta:
                precio_normal = precio_oferta

            precio_metro = round(precio_oferta / prod["metros_totales"], 1)
            descuento = round((1 - (precio_oferta / precio_normal)) * 100) if precio_normal > precio_oferta else 0

            if descuento > 0:
                estado = f"🔥 {descuento}% DCTO"
            elif not info_precio.get("stock", True):
                estado = "❌ Sin Stock"
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
                "estado": "Sin Conexión",
                "url": prod["url"]
            })

    return resultados

def calcular_resumen_comercial(datos):
    validos = [d for d in datos if d["precio_metro_num"] > 0]
    if not validos:
        return {
            "resumen": "No se pudieron obtener datos desde el e-commerce.",
            "estrategia": "Verificar disponibilidad del servidor.",
            "alertas": ["Error al consultar precios en vivo."]
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
st.caption("Benchmarking en tiempo real • Extracción directa de checkout")

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
