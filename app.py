import streamlit as st
import pandas as pd
import yaml
import requests
import re
import time
import random
import subprocess
import sys
from pathlib import Path

st.set_page_config(
    page_title="Dipisa & Ovella — Monitor de Pricing",
    page_icon="📊",
    layout="wide"
)

BASE_DIR = Path(__file__).parent
PRODUCTOS_PATH = BASE_DIR / "productos.csv"
RETAILERS_PATH = BASE_DIR / "retailers.yaml"


@st.cache_resource
def asegurar_chromium_instalado():
    """Instala el navegador headless de Playwright la primera vez que arranca la
    app en el servidor (Streamlit Cloud no lo trae preinstalado). cache_resource
    hace que esto corra una sola vez por instancia, no en cada refresh."""
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True, capture_output=True, timeout=300,
        )
        return True
    except Exception as e:
        return f"No se pudo instalar Chromium: {str(e)[:200]}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-CL,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@st.cache_data(ttl=3600)
def cargar_config():
    productos = pd.read_csv(PRODUCTOS_PATH, comment="#", skip_blank_lines=True)
    productos = productos.dropna(subset=["sku_interno"])  # ignora filas vacías
    with open(RETAILERS_PATH, "r", encoding="utf-8") as f:
        retailers = yaml.safe_load(f)
    return productos, retailers


def _fragmento_diagnostico(texto: str) -> str:
    """Devuelve un fragmento corto y limpio del HTML recibido, para ver en el
    mensaje de error si el sitio devolvió la página real o un bloqueo/captcha."""
    limpio = re.sub(r"<[^>]+>", " ", texto)  # quita tags para que se lea
    limpio = re.sub(r"\s+", " ", limpio).strip()
    return limpio[:180]


def _consultar_meta_tag(url: str, patron_precio: str, patron_disp: str, intentos: int = 3):
    """Lee precio desde meta tags del HTML crudo (rápido, sin navegador)."""
    for intento in range(1, intentos + 1):
        try:
            res = requests.get(url, headers=HEADERS, timeout=12)
            if res.status_code == 200:
                m = re.search(patron_precio, res.text)
                if not m:
                    frag = _fragmento_diagnostico(res.text)
                    return None, True, f"No se encontró el precio. Recibido: \"{frag}\""
                precio = float(m.group(1).replace(".", "").replace(",", "."))

                disponible = True
                if patron_disp:
                    m_disp = re.search(patron_disp, res.text)
                    if m_disp:
                        disponible = "in stock" in m_disp.group(1).lower()

                return precio, disponible, None
            elif res.status_code in (403, 429):
                time.sleep(random.uniform(2, 4) * intento)
                continue
            else:
                return None, False, f"HTTP {res.status_code}"
        except requests.RequestException as e:
            if intento == intentos:
                return None, False, f"Error de conexión: {str(e)[:100]}"
            time.sleep(random.uniform(1.5, 3) * intento)
    return None, False, "Bloqueado tras varios intentos (403/429)"


def _consultar_text_pattern(url: str, cfg: dict, intentos: int = 3):
    """Lee precio oferta y normal buscando dos patrones de texto en el HTML crudo
    (sin meta tags, sin navegador). Útil para sitios Next.js con SSR que ya traen
    ambos precios en el texto renderizado desde el servidor."""
    patron_oferta = cfg["patron_precio_oferta"]
    patron_normal = cfg.get("patron_precio_normal")

    for intento in range(1, intentos + 1):
        try:
            res = requests.get(url, headers=HEADERS, timeout=12)
            if res.status_code == 200:
                m_oferta = re.search(patron_oferta, res.text)
                if not m_oferta:
                    frag = _fragmento_diagnostico(res.text)
                    return None, None, True, f"No se encontró el precio. Recibido: \"{frag}\""
                precio_oferta = float(m_oferta.group(1).replace(".", "").replace(",", ""))

                precio_normal = precio_oferta
                if patron_normal:
                    m_normal = re.search(patron_normal, res.text)
                    if m_normal:
                        precio_normal = float(m_normal.group(1).replace(".", "").replace(",", ""))

                return precio_oferta, precio_normal, True, None
            elif res.status_code in (403, 429):
                time.sleep(random.uniform(2, 4) * intento)
                continue
            else:
                return None, None, False, f"HTTP {res.status_code}"
        except requests.RequestException as e:
            if intento == intentos:
                return None, None, False, f"Error de conexión: {str(e)[:100]}"
            time.sleep(random.uniform(1.5, 3) * intento)
    return None, None, False, "Bloqueado tras varios intentos (403/429)"


def _consultar_playwright(url: str, cfg: dict, intentos: int = 3):
    """Renderiza con navegador headless y lee precio(s) del DOM. Requiere Playwright instalado."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, None, False, "Playwright no está instalado (ver requirements.txt)"

    selector_oferta = cfg.get("selector_precio_oferta")
    selector_normal = cfg.get("selector_precio_normal")

    for intento in range(1, intentos + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=HEADERS["User-Agent"], locale="es-CL",
                    viewport={"width": 1366, "height": 768},
                )
                page = context.new_page()
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
                time.sleep(random.uniform(1.0, 2.0))

                precio_oferta = None
                precio_normal = None
                try:
                    precio_oferta = float(re.sub(r"[^\d]", "", page.locator(selector_oferta).first.inner_text()))
                except Exception:
                    pass
                if selector_normal:
                    try:
                        precio_normal = float(re.sub(r"[^\d]", "", page.locator(selector_normal).first.inner_text()))
                    except Exception:
                        pass

                browser.close()
                if precio_oferta:
                    return precio_oferta, precio_normal or precio_oferta, True, None
                if intento == intentos:
                    return None, None, False, "No se encontró el precio en el DOM renderizado"
        except Exception as e:
            if intento == intentos:
                return None, None, False, f"Error Playwright: {str(e)[:100]}"
        time.sleep(random.uniform(2, 4) * intento)
    return None, None, False, "Falla desconocida"


@st.cache_data(ttl=1800)
def consultar_precios_en_vivo(_productos_hash: str):
    productos, retailers_cfg = cargar_config()
    resultados = []

    usa_playwright = any(cfg.get("metodo") == "playwright" for cfg in retailers_cfg.values())
    if usa_playwright:
        resultado_instalacion = asegurar_chromium_instalado()
        if resultado_instalacion is not True:
            st.warning(f"⚠️ {resultado_instalacion} — los retailers con Playwright pueden fallar.")

    for _, prod in productos.iterrows():
        retailer_key = prod["retailer"]
        cfg = retailers_cfg.get(retailer_key)

        if cfg is None:
            resultados.append({**prod.to_dict(), "precio_normal_num": 0, "precio_oferta_num": 0,
                                "estado": f"Retailer '{retailer_key}' no está en retailers.yaml"})
            continue

        if cfg["metodo"] == "meta_tag":
            precio, disponible, error = _consultar_meta_tag(
                prod["url"], cfg["selector_precio"], cfg.get("selector_disponibilidad")
            )
            precio_normal = precio
        elif cfg["metodo"] == "text_pattern":
            precio, precio_normal, disponible, error = _consultar_text_pattern(prod["url"], cfg)
        elif cfg["metodo"] == "playwright":
            precio, precio_normal, disponible, error = _consultar_playwright(prod["url"], cfg)
        else:
            precio, precio_normal, disponible, error = None, None, False, f"Método desconocido: {cfg['metodo']}"

        time.sleep(random.uniform(0.8, 1.8))  # espacio entre productos

        if precio and precio > 0:
            precio_metro = round(precio / prod["metros_totales"], 1) if prod.get("metros_totales") else None
            estado = "Disponible" if disponible else "❌ Sin Stock"
            resultados.append({
                **prod.to_dict(),
                "retailer_nombre": cfg["nombre"],
                "precio_normal_num": precio_normal or precio,
                "precio_oferta_num": precio,
                "precio_metro_num": precio_metro or 0,
                "estado": estado,
            })
        else:
            resultados.append({
                **prod.to_dict(),
                "retailer_nombre": cfg["nombre"] if cfg else retailer_key,
                "precio_normal_num": 0,
                "precio_oferta_num": 0,
                "precio_metro_num": 0,
                "estado": f"Sin Conexión ({error})" if error else "Sin Conexión",
            })

    return resultados


def calcular_resumen_comercial(datos):
    validos = [d for d in datos if d["precio_metro_num"] > 0]
    if not validos:
        return {
            "resumen": "No se pudo obtener ningún precio en esta corrida. Revisa la columna Estado.",
            "estrategia": "Verificar conectividad o si algún sitio cambió su estructura.",
            "alertas": ["Sin datos en vivo."],
        }

    mas_barato = min(validos, key=lambda x: x["precio_metro_num"])
    mas_caro = max(validos, key=lambda x: x["precio_metro_num"])
    promedio_m = round(sum(d["precio_metro_num"] for d in validos) / len(validos), 1)

    resumen = (
        f"El precio promedio de mercado se sitúa en **${promedio_m}/m**. "
        f"La opción más económica por metro es **{mas_barato['marca']} ({mas_barato['producto']})** "
        f"en **{mas_barato['retailer_nombre']}** con **${mas_barato['precio_metro_num']}/m**."
    )

    precio_objetivo = round(mas_barato["precio_metro_num"] * 0.95, 1)
    estrategia = (
        f"Para liderar en competitividad, el precio objetivo sugerido debe ser igual o inferior a "
        f"**${precio_objetivo}/m**."
    )

    alertas = [f"📌 **Piso de Categoría:** {mas_barato['marca']} marca el mínimo en ${mas_barato['precio_metro_num']}/m."]
    if mas_caro["precio_metro_num"] > mas_barato["precio_metro_num"]:
        diff = round(((mas_caro["precio_metro_num"] / mas_barato["precio_metro_num"]) - 1) * 100)
        alertas.append(f"📈 **Diferencial:** {mas_caro['marca']} cuesta un {diff}% más por metro que el más barato.")

    return {"resumen": resumen, "estrategia": estrategia, "alertas": alertas}


# --- Interfaz ---
st.title("📊 Dipisa & Ovella — Monitor de Pricing en Vivo")
st.caption("Lee config/productos.csv y config/retailers.yaml — agregar tiendas/productos no requiere tocar código")

productos_df, _ = cargar_config()
hash_productos = str(pd.util.hash_pandas_object(productos_df).sum())  # invalida cache si cambia el CSV
datos_tabla = consultar_precios_en_vivo(hash_productos)
analisis = calcular_resumen_comercial(datos_tabla)

col1, col2 = st.columns([2, 1])
with col1:
    st.subheader("💡 Resumen Comercial")
    st.markdown(analisis["resumen"])
    st.info(f"🎯 **Estrategia:** {analisis['estrategia']}")
with col2:
    st.subheader("⚠️ Alertas del Mercado")
    for a in analisis["alertas"]:
        st.warning(a)

st.subheader("📋 Tabla Comparativa de Precios")
tabla_mostrar = [
    {
        "Retailer": d.get("retailer_nombre", d.get("retailer")),
        "Marca": d.get("marca"),
        "Producto": d.get("producto"),
        "Precio Normal": f"${d['precio_normal_num']:,.0f}".replace(",", ".") if d["precio_normal_num"] else "N/D",
        "Precio Oferta": f"${d['precio_oferta_num']:,.0f}".replace(",", ".") if d["precio_oferta_num"] else "N/D",
        "$/Metro": f"${d['precio_metro_num']}/m" if d["precio_metro_num"] else "N/D",
        "Estado": d["estado"],
    }
    for d in datos_tabla
]
try:
    st.dataframe(tabla_mostrar, width="stretch")
except TypeError:
    st.dataframe(tabla_mostrar, use_container_width=True)

if st.button("🔄 Forzar Recarga"):
    st.cache_data.clear()
    st.rerun()

with st.expander("➕ ¿Cómo agrego productos o tiendas nuevas?"):
    st.markdown(
        "- **Producto nuevo (misma tienda):** agrega una fila en `config/productos.csv`.\n"
        "- **Tienda nueva:** agrega un bloque en `config/retailers.yaml` "
        "(ver los comentarios del archivo con instrucciones paso a paso).\n"
        "- No hace falta editar `app.py` para ninguno de los dos casos."
    )
