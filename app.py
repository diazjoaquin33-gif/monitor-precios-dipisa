import streamlit as st
import json
import requests

st.set_page_config(
    page_title="Dipisa & Ovella — Monitor de Pricing",
    page_icon="📊",
    layout="wide"
)

# Pega aquí la URL que te entregó Google Apps Script
PUENTE_URL = "https://script.google.com/macros/s/AKfycbw6EL_nzt7CuFUJAWj1i04_xWXwOKTX6VXHeYZDp2-rYa9AJLyflIQ3oD6PDIbqsWDT/exec"

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

@st.cache_data(ttl=1800)
def consultar_precios_en_vivo():
    resultados = []

    for prod in PRODUCTOS:
        precio_oferta = None
        precio_normal = None
        stock = True

        try:
            res = requests.get(f"{PUENTE_URL}?sku={prod['sku_id']}", timeout=10)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, list) and len(data) > 0:
                    offer = data[0]["items"][0]["sellers"][0]["commertialOffer"]
                    p_price = int(offer.get("Price", 0))
                    p_list = int(offer.get("ListPrice", p_price))
                    p_spot = int(offer.get("spotPrice", p_price))

                    precio_oferta = min([p for p in [p_spot, p_price] if p > 0]) if (p_spot or p_price) else p_price
                    precio_normal = p_list if p_list > 0 else precio_oferta
                    stock = offer.get("AvailableQuantity", 0) > 0
        except Exception:
            pass

        if precio_oferta and precio_oferta > 0:
            if precio_normal < precio_oferta:
                precio_normal = precio_oferta

            precio_metro = round(precio_oferta / prod["metros_totales"], 1)
            descuento = round((1 - (precio_oferta / precio_normal)) * 100) if precio_normal > precio_oferta else 0

            if descuento > 0:
                estado = f"🔥 {descuento}% DCTO"
            elif not stock:
                estado = "❌ Sin Stock"
            else:
                estado = "Disponible"

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
            "resumen": "Configura la URL de Google Apps Script para comenzar a recibir precios en vivo.",
            "estrategia": "Verificar enlace del puente.",
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
st.caption("Benchmarking en tiempo real • 100% Automático y libre de límites")

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
