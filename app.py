import streamlit as st
import json
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
        "slug": "papel-higienico-doble-hoja-23-m-4-un-1859328",
        "url": "https://www.santaisabel.cl/papel-higienico-doble-hoja-23-m-4-un-1859328/p"
    },
    {
        "marca": "Noble",
        "sku_nombre": "Doble Hoja 22m 40 un",
        "retailer": "Santa Isabel",
        "metros_totales": 880,
        "sku_id": "1960588",
        "slug": "papel-higienico-noble-doble-hoja-22-m-40-un-1960588",
        "url": "https://www.santaisabel.cl/ph-doble-hoja-noble-dh-40-rollos-1960588/p"
    },
    {
        "marca": "Confort",
        "sku_nombre": "Doble Hoja 22m 40 un",
        "retailer": "Santa Isabel",
        "metros_totales": 880,
        "sku_id": "1997284",
        "slug": "papel-higienico-confort-doble-hoja-22-m-40-un-1997284",
        "url": "https://www.santaisabel.cl/papel-higienico-confort-dh-22mt-40un-1997284/p"
    }
]

@st.cache_data(ttl=1800)
def extraer_precios_api():
    resultados = []
    session = requests.Session()
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.santaisabel.cl",
        "Referer": "https://www.santaisabel.cl/"
    }

    for prod in PRODUCTOS:
        precio_oferta = None
        precio_normal = None
        stock = False
        error_msg = None

        # 1. Consulta GraphQL directa (Endpoint oficial del frontend de Santa Isabel)
        try:
            graphql_url = "https://www.santaisabel.cl/_v/segment/graphql/v1"
            query_payload = {
                "query": """
                query GetProductPrice($slug: String!) {
                    product(slug: $slug) {
                        items {
                            itemId
                            sellers {
                                commertialOffer {
                                    Price
                                    ListPrice
                                    spotPrice
                                    AvailableQuantity
                                }
                            }
                        }
                    }
                }
                """,
                "variables": {"slug": prod["slug"]}
            }
            
            res = session.post(graphql_url, json=query_payload, headers=headers, timeout=8)
            if res.status_code == 200:
                data = res.json()
                product_data = data.get("data", {}).get("product")
                if product_data and product_data.get("items"):
                    seller_offer = product_data["items"][0]["sellers"][0]["commertialOffer"]
                    p_price = int(seller_offer.get("Price", 0))
                    p_list = int(seller_offer.get("ListPrice", p_price))
                    p_spot = int(seller_offer.get("spotPrice", p_price))
                    
                    precio_oferta = p_spot if p_spot > 0 else p_price
                    precio_normal = p_list if p_list > 0 else precio_oferta
                    stock = seller_offer.get("AvailableQuantity", 0) > 0
        except Exception as e:
            error_msg = str(e)

        # 2. Respaldo por API REST pública de catálogo VTEX
        if not precio_oferta or precio_oferta == 0:
            try:
                rest_url = f"https://www.santaisabel.cl/api/catalog_system/pub/products/search/{prod['slug']}?sc=1"
                res_rest = session.get(rest_url, headers=headers, timeout=8)
                if res_rest.status_code == 200:
                    rest_data = res_rest.json()
                    if rest_data and len(rest_data) > 0:
                        offer = rest_data[0]["items"][0]["sellers"][0]["commertialOffer"]
                        p_price = int(offer.get("Price", 0))
                        p_list = int(offer.get("ListPrice", p_price))
                        p_spot = int(offer.get("spotPrice", p_price))
                        
                        precio_oferta = p_spot if p_spot > 0 else p_price
                        precio_normal = p_list if p_list > 0 else precio_oferta
                        stock = offer.get("AvailableQuantity", 0) > 0
            except Exception as e:
                error_msg = str(e)

        # Validación final de captura
        if precio_oferta and precio_oferta > 0:
            precio_metro = round(precio_oferta / prod["metros_totales"], 1)
            if precio_oferta < precio_normal:
                estado_tag = "🔥 En Oferta"
            elif not stock:
                estado_tag = "❌ Sin Stock"
            else:
                estado_tag = "Normal"

            resultados.append({
                "marca": prod["marca"],
                "sku": prod["sku_nombre"],
                "retailer": prod["retailer"],
                "metros_totales": prod["metros_totales"],
                "precio_normal": f"${precio_normal:,.0f}".replace(",", "."),
                "precio_oferta": f"${precio_oferta:,.0f}".replace(",", "."),
                "precio_metro": f"${precio_metro} /m",
                "precio_metro_num": precio_metro,
                "estado": estado_tag,
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
                "estado": f"Sin Datos ({error_msg or 'Bloqueo Retailer'})",
                "url": prod["url"]
            })
            
    return resultados

def generar_analisis_automatico(datos):
    validos = [d for d in datos if d["precio_metro_num"] > 0]
    if not validos:
        return {
            "resumen": "No se pudieron obtener datos válidos desde el servidor del retailer.",
            "estrategia": "Verificar la disponibilidad de conexión con Santa Isabel.",
            "alertas": ["Error al consultar precios en vivo."]
        }
    
    mas_barato = min(validos, key=lambda x: x["precio_metro_num"])
    mas_caro = max(validos, key=lambda x: x["precio_metro_num"])
    promedio_m = round(sum(d["precio_metro_num"] for d in validos) / len(validos), 1)

    resumen = (
        f"El mercado promedia **${promedio_m}/m**. "
        f"La opción más económica por metro es **{mas_barato['marca']} ({mas_barato['sku']})** a **{mas_barato['precio_metro']}**."
    )
    
    precio_objetivo = round(mas_barato["precio_metro_num"] * 0.95, 1)
    estrategia = (
        f"Para posicionar a **Ovella** como líder de conveniencia en retail, el precio objetivo sugerido debe ser inferior a "
        f"**${precio_objetivo}/m** frente a los formatos familiares de 880m."
    )
    
    alertas = [
        f"📌 **Piso de la Categoría:** {mas_barato['marca']} fija la paridad mínima en {mas_barato['precio_metro']}.",
        f"📈 **Diferencial de Formato:** El pack de menor volumen es un {round(((mas_caro['precio_metro_num']/mas_barato['precio_metro_num'])-1)*100)}% más caro por metro que el pack ahorro."
    ]

    return {
        "resumen": resumen,
        "estrategia": estrategia,
        "alertas": alertas
    }

# --- Interfaz Gráfica ---
st.title("📊 Dipisa & Ovella — Monitor de Pricing en Vivo")
st.caption("Benchmarking en tiempo real • Extracción directa de catálogo de retail")

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

try:
    st.dataframe(columnas_mostrar, width="stretch")
except TypeError:
    st.dataframe(columnas_mostrar, use_container_width=True)

col_btn1, col_btn2 = st.columns([1, 4])
with col_btn1:
    if st.button("🔄 Forzar Recarga"):
        st.cache_data.clear()
        st.rerun()
