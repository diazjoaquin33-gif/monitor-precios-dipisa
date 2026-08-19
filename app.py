import streamlit as st
import requests
import re
import time
import random

st.set_page_config(
    page_title="Dipisa & Ovella — Monitor de Pricing",
    page_icon="📊",
    layout="wide"
)

# ---------------------------------------------------------------------------
# Ya NO se usa Google Apps Script como puente. El precio se lee directo desde
# el HTML de la página de producto: los sitios VTEX (como Santa Isabel) traen
# el precio en meta tags Open Graph incluso sin ejecutar JavaScript, ej:
#   <meta property="product:price:amount" content="1950"/>
#   <meta property="product:availability" content="in stock"/>
# Esto es más simple y más rápido que scraping con navegador o que depender
# de un intermediario externo.
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-CL,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Patrones de meta tags a buscar en el HTML (algunos sitios VTEX varían el nombre)
PATRON_PRECIO = re.compile(r'property="product:price:amount"\s+content="([\d.,]+)"')
PATRON_PRECIO_ALT = re.compile(r'content="([\d.,]+)"\s+property="product:price:amount"')
PATRON_DISPONIBILIDAD = re.compile(r'property="product:availability"\s+content="([^"]+)"')

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


def _extraer_precio(html: str):
    m = PATRON_PRECIO.search(html) or PATRON_PRECIO_ALT.search(html)
    if not m:
        return None
    texto = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _extraer_disponibilidad(html: str) -> bool:
    m = PATRON_DISPONIBILIDAD.search(html)
    if not m:
        return True  # si no viene el tag, asumimos disponible por defecto
    return "in stock" in m.group(1).lower()


def _consultar_producto(prod: dict, intentos: int = 3):
    for intento in range(1, intentos + 1):
        try:
            res = requests.get(prod["url"], headers=HEADERS, timeout=12)
            if res.status_code == 200:
                precio = _extraer_precio(res.text)
                disponible = _extraer_disponibilidad(res.text)
                if precio is not None:
                    return precio, disponible, None
                return None, False, "No se encontró el precio en el HTML (¿cambió el sitio?)"
            elif res.status_code in (403, 429):
                # posible bloqueo: esperar más y reintentar con backoff
                time.sleep(random.uniform(2, 4) * intento)
                continue
            else:
                return None, False, f"HTTP {res.status_code}"
        except requests.RequestException as e:
            if intento == intentos:
                return None, False, f"Error de conexión: {str(e)[:100]}"
            time.sleep(random.uniform(1.5, 3) * intento)
    return None, False, "Bloqueado tras varios intentos (403/429)"


@st.cache_data(ttl=1800)
def consultar_precios_en_vivo():
    resultados = []

    for prod in PRODUCTOS:
        precio_oferta, stock, error = _consultar_producto(prod)
        # jitter entre productos para no disparar requests en ráfaga
        time.sleep(random.uniform(0.8, 1.8))

        if precio_oferta and precio_oferta > 0:
            precio_normal = precio_oferta  # el meta tag solo trae precio final; sin descuento detectable por esta vía
            precio_metro = round(precio_oferta / prod["metros_totales"], 1)

            estado = "Disponible" if stock else "❌ Sin Stock"

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
                "estado": f"Sin Conexión ({error})" if error else "Sin Conexión",
                "url": prod["url"]
            })

    return resultados


def calcular_resumen_comercial(datos):
    validos = [d for d in datos if d["precio_metro_num"] > 0]
    if not validos:
        return {
            "resumen": "No se pudo obtener ningún precio en esta corrida. Revisa el detalle de error en la columna Estado.",
            "estrategia": "Verificar conectividad o si los sitios cambiaron su estructura HTML.",
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
        if mas_barato["precio_metro_num"] > 0 else "Sin datos suficientes para comparar formatos."
    ]

    return {
        "resumen": resumen,
        "estrategia": estrategia,
        "alertas": alertas
    }


# --- Interfaz Gráfica ---
st.title("📊 Dipisa & Ovella — Monitor de Pricing en Vivo")
st.caption("Benchmarking en tiempo real • lectura directa del HTML, sin intermediarios")

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
