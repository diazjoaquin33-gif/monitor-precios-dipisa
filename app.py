import streamlit as st
import json
import re
import urllib.parse
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
        "url": "https://www.santaisabel.cl/papel-higienico-confort-dh-22mt-40un-1997284/p"
    }
]

def extraer_precios_html(html_text):
    """Extrae precio normal, precio oferta y promociones desde el estado VTEX o metadatos."""
    precio_oferta = None
    precio_normal = None

    # 1. Búsqueda profunda en el estado reactivo de VTEX (__STATE__)
    try:
        state_match = re.search(r'__STATE__\s*=\s*(\{.*?\});?</script>', html_text, re.DOTALL)
        if state_match:
            state_data = json.loads(state_match.group(1))
            for key, val in state_data.items():
                if isinstance(val, dict) and "commertialOffer" in key:
                    p_price = int(val.get("Price", 0))
                    p_list = int(val.get("ListPrice", p_price))
                    p_spot = int(val.get("spotPrice", p_price))

                    p_final = min([p for p in [p_spot, p_price] if p > 0]) if (p_spot or p_price) else p_price
                    if p_final > 0:
                        precio_oferta = p_final
                        precio_normal = p_list if p_list > 0 else precio_oferta
                        break
    except Exception:
        pass

    # 2. Respaldo por JSON-LD
    if not precio_oferta:
        try:
            ld_blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html_text, re.DOTALL)
            for block in ld_blocks:
                data = json.loads(block)
                if "offers" in data:
                    offers = data["offers"]
                    if isinstance(offers, list):
                        offers = offers[0]
                    p = float(offers.get("price", offers.get("lowPrice", 0)))
                    high_p = float(offers.get("highPrice", p))
                    if p > 0:
                        precio_oferta = int(p)
                        precio_normal = int(high_p) if high_p > p else int(p)
                        break
        except Exception:
            pass

    # 3. Respaldo por búsqueda de patrones numéricos de precios en HTML
    if not precio_oferta:
        precios = re.findall(r'\$\s?([0-9]{1,3}(?:\.[0-9]{3})+)', html_text)
        if precios:
            valores = sorted(list(set([int(p.replace(".", "")) for p in precios if int(p.replace(".", "")) > 500])))
            if len(valores) == 1:
                precio_oferta = valores[0]
                precio_normal = valores[0]
            elif len(valores) >= 2:
                precio_oferta = valores[0]  # El menor es la oferta
                precio_normal = valores[-1] # El mayor suele ser el precio de lista

    return precio_oferta, precio_normal

@st.cache_data(ttl=1800)
def consultar_precios_en_vivo():
    resultados = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "es-CL,es;q=0.9"
    }

    for prod in PRODUCTOS:
        precio_oferta = None
        precio_normal = None
        html_content = ""

        # Intento directo
        try:
            r = requests.get(prod["url"], headers=headers, timeout=5)
            if r.status_code == 200:
                html_content = r.text
                precio_oferta, precio_normal = extraer_precios_html(html_content)
        except Exception:
            pass

        # Intento por proxy si la conexión directa no trae el HTML completo
        if not precio_oferta or precio_oferta == 0:
            try:
                proxy_url = f"https://api.allorigins.win/get?url={urllib.parse.quote(prod['url'])}"
                r = requests.get(proxy_url, headers=headers, timeout=10)
                if r.status_code == 200:
                    html_content = r.json().get("contents", "")
                    precio_oferta, precio_normal = extraer_precios_html(html_content)
            except Exception:
                pass

        if precio_oferta and precio_oferta > 0:
            if not precio_normal or precio_normal < precio_oferta:
                precio_normal = precio_oferta

            precio_metro = round(precio_oferta / prod["metros_totales"], 1)
            descuento = round((1 - (precio_oferta / precio_normal)) * 100) if precio_normal > precio_oferta else 0

            if descuento > 0:
                estado = f"🔥 {descuento}% DCTO"
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

col_btn1, _ = st.columns([1, 4])
with col_btn1:
    if st.button("🔄 Forzar Recarga"):
        st.cache_data.clear()
        st.rerun()
