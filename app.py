import streamlit as st
import pandas as pd
import yaml
import requests
import re
import time
import random
import subprocess
import sys
import concurrent.futures
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

ZONA_HORARIA_CL = ZoneInfo("America/Santiago")

# Paleta validada (ver skill de dataviz): status fijo (nunca se usa para
# series/categorías) y azul como acento principal.
COLOR_BUENO = "#0ca30c"
COLOR_CRITICO = "#d03b3b"
COLOR_ACENTO = "#2a78d6"
COLOR_MUTED = "#898781"

st.set_page_config(
    page_title="Dipisa & Ovella — Monitor de Pricing",
    page_icon="📊",
    layout="wide"
)

BASE_DIR = Path(__file__).parent
PRODUCTOS_PATH = BASE_DIR / "productos.csv"
RETAILERS_PATH = BASE_DIR / "retailers.yaml"
OVELLA_PATH = BASE_DIR / "ovella.csv"


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


@st.cache_data(ttl=3600)
def cargar_ovella():
    ovella = pd.read_csv(OVELLA_PATH, comment="#", skip_blank_lines=True)
    return ovella.dropna(subset=["sku_ovella"])


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
    ambos precios renderizados desde el servidor."""
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
                precio_oferta = float(m_oferta.group(1).replace(".", "").replace(",", "."))

                precio_normal = precio_oferta
                if patron_normal:
                    m_normal = re.search(patron_normal, res.text)
                    if m_normal:
                        precio_normal = float(m_normal.group(1).replace(".", "").replace(",", "."))

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


def _consultar_playwright(context, url: str, cfg: dict, intentos: int = 3):
    """Lee precio(s) del DOM con un selector CSS, usando una pestaña nueva de
    un browser context YA ABIERTO (context lo crea y cierra quien llama, una
    sola vez por lote de productos del mismo retailer — abrir un navegador
    completo por producto es lo que hacía esto lento)."""
    selector_oferta = cfg.get("selector_precio_oferta")
    selector_normal = cfg.get("selector_precio_normal")

    for intento in range(1, intentos + 1):
        page = None
        try:
            page = context.new_page()
            page.goto(url, timeout=15000, wait_until="domcontentloaded")
            time.sleep(random.uniform(0.6, 1.2))

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

            if precio_oferta:
                return precio_oferta, precio_normal or precio_oferta, True, None
            if intento == intentos:
                return None, None, False, "No se encontró el precio en el DOM renderizado"
        except Exception as e:
            if intento == intentos:
                return None, None, False, f"Error Playwright: {str(e)[:100]}"
        finally:
            if page is not None:
                page.close()
        time.sleep(random.uniform(2, 4) * intento)
    return None, None, False, "Falla desconocida"


def _consultar_playwright_text(context, url: str, cfg: dict, intentos: int = 3):
    """Como _consultar_playwright, pero busca el precio con un patrón de texto
    (patron_precio_oferta / patron_precio_normal) sobre el texto ya renderizado
    por el navegador, en vez de un selector CSS. Es más robusto en sitios cuyo
    framework (ej. Next.js) genera nombres de clase distintos en cada deploy,
    donde un selector CSS se rompe solo, pero el texto visible no cambia.
    Reusa el context abierto por el llamador en vez de lanzar un navegador
    nuevo por producto."""
    patron_oferta = cfg["patron_precio_oferta"]
    patron_normal = cfg.get("patron_precio_normal")

    for intento in range(1, intentos + 1):
        page = None
        try:
            page = context.new_page()
            page.goto(url, timeout=20000, wait_until="networkidle")
            time.sleep(random.uniform(0.6, 1.2))
            texto = page.inner_text("body")

            m_oferta = re.search(patron_oferta, texto)
            if not m_oferta:
                frag = _fragmento_diagnostico(texto)
                if intento == intentos:
                    return None, None, True, f"No se encontró el precio. Recibido: \"{frag}\""
                time.sleep(random.uniform(2, 4) * intento)
                continue
            precio_oferta = float(m_oferta.group(1).replace(".", "").replace(",", "."))

            precio_normal = precio_oferta
            if patron_normal:
                m_normal = re.search(patron_normal, texto)
                if m_normal:
                    precio_normal = float(m_normal.group(1).replace(".", "").replace(",", "."))

            return precio_oferta, precio_normal, True, None
        except Exception as e:
            if intento == intentos:
                return None, None, False, f"Error Playwright: {str(e)[:100]}"
            time.sleep(random.uniform(2, 4) * intento)
        finally:
            if page is not None:
                page.close()
    return None, None, False, "Falla desconocida"


def _resolver_ruta(data, ruta):
    """Resuelve una ruta tipo 'sellers.0.price' contra un dict/list JSON
    anidado, devolviendo None si algún tramo no existe."""
    actual = data
    for tramo in ruta.split("."):
        try:
            if isinstance(actual, list):
                actual = actual[int(tramo)]
            else:
                actual = actual[tramo]
        except (KeyError, IndexError, ValueError, TypeError):
            return None
    return actual


def _consultar_playwright_json(context, url: str, cfg: dict, intentos: int = 3):
    """Para sitios que bloquean requests directos (Akamai/Cloudflare) pero
    cuyo propio frontend consulta una API JSON interna para el precio: en vez
    de renderizar la página completa por producto, hace UNA sola visita de
    'caldeo' (warmup_url) para que el navegador pase el desafío anti-bot y
    reciba las cookies de sesión, y de ahí en adelante pide el JSON de cada
    producto directo con context.request (mismas cookies, sin abrir pestañas
    ni re-renderizar nada) — mucho más rápido que playwright/playwright_text.
    El slug se saca del último tramo de la URL del producto."""
    slug = url.rstrip("/").split("/")[-1]
    api_url = cfg["api_base"].rstrip("/") + "/" + slug

    for intento in range(1, intentos + 1):
        try:
            resp = context.request.get(api_url, headers={"Accept": "application/json"})
            if resp.ok:
                data = resp.json()
                precio = _resolver_ruta(data, cfg["campo_precio"])
                if precio is None:
                    if intento == intentos:
                        return None, None, True, f"El JSON no trajo '{cfg['campo_precio']}'"
                    time.sleep(random.uniform(1.5, 3) * intento)
                    continue
                precio_normal = precio
                if cfg.get("campo_precio_normal"):
                    valor_normal = _resolver_ruta(data, cfg["campo_precio_normal"])
                    if valor_normal is not None:
                        precio_normal = valor_normal
                return float(precio), float(precio_normal), True, None
            elif resp.status in (403, 429):
                if intento == intentos:
                    return None, None, False, f"Bloqueado (HTTP {resp.status}) incluso tras el warmup"
                time.sleep(random.uniform(2, 4) * intento)
            else:
                return None, None, False, f"HTTP {resp.status}"
        except Exception as e:
            if intento == intentos:
                return None, None, False, f"Error: {str(e)[:100]}"
            time.sleep(random.uniform(1.5, 3) * intento)
    return None, None, False, "Falla desconocida"


def _procesar_lote_http(cfg, lista_productos):
    """Retailers meta_tag/text_pattern: requests HTTP livianos, secuenciales
    (mismo ritmo de siempre) dentro de su propio hilo."""
    salida = []
    for prod in lista_productos:
        if cfg["metodo"] == "meta_tag":
            precio, disponible, error = _consultar_meta_tag(
                prod["url"], cfg["selector_precio"], cfg.get("selector_disponibilidad")
            )
            precio_normal = precio
        else:  # text_pattern
            precio, precio_normal, disponible, error = _consultar_text_pattern(prod["url"], cfg)
        salida.append((prod, precio, precio_normal, disponible, error))
        time.sleep(random.uniform(0.5, 1.0))
    return salida


def _procesar_lote_playwright(cfg, lista_productos):
    """Retailers playwright/playwright_text/playwright_json: abre UN solo
    navegador para todo el lote de este retailer (antes se abría uno nuevo
    por producto) y procesa los productos secuencialmente dentro de él —
    intentamos en paralelo (varias pestañas a la vez) y Tottus empezó a ser
    bloqueado por Cloudflare al tercer producto, así que esto se mantiene
    secuencial a propósito. La ganancia de velocidad viene de no relanzar el
    navegador, y de que retailers distintos ya corren en paralelo entre sí
    (ver consultar_precios_en_vivo)."""
    from playwright.sync_api import sync_playwright

    salida = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"], locale="es-CL",
            viewport={"width": 1366, "height": 768},
        )
        if cfg["metodo"] == "playwright_json":
            # Una sola visita real para pasar el desafío anti-bot y dejar
            # las cookies de sesión puestas en el context. "networkidle" no
            # sirve acá: la portada de Alvi tiene actividad de red continua
            # (analytics, chat) que nunca llega a quedar inactiva y hace
            # timeout — con domcontentloaded + una espera corta alcanza para
            # que el desafío anti-bot ya haya corrido y dejado las cookies.
            pagina_calentamiento = context.new_page()
            pagina_calentamiento.goto(cfg["warmup_url"], timeout=25000, wait_until="domcontentloaded")
            pagina_calentamiento.wait_for_timeout(4000)
            pagina_calentamiento.close()

        for prod in lista_productos:
            if cfg["metodo"] == "playwright":
                precio, precio_normal, disponible, error = _consultar_playwright(context, prod["url"], cfg)
            elif cfg["metodo"] == "playwright_json":
                precio, precio_normal, disponible, error = _consultar_playwright_json(context, prod["url"], cfg)
            else:  # playwright_text
                precio, precio_normal, disponible, error = _consultar_playwright_text(context, prod["url"], cfg)
            salida.append((prod, precio, precio_normal, disponible, error))
            time.sleep(random.uniform(0.5, 1.2))
        browser.close()
    return salida


@st.cache_data(ttl=1800)
def consultar_precios_en_vivo(productos_hash: str):
    productos, retailers_cfg = cargar_config()

    usa_playwright = any(
        cfg.get("metodo") in ("playwright", "playwright_text", "playwright_json")
        for cfg in retailers_cfg.values()
    )
    if usa_playwright:
        resultado_instalacion = asegurar_chromium_instalado()
        if resultado_instalacion is not True:
            st.warning(f"⚠️ {resultado_instalacion} — los retailers con Playwright pueden fallar.")

    # Agrupa por retailer y procesa cada grupo en su propio hilo: distintos
    # sitios se consultan al mismo tiempo (más rápido en total), pero dentro
    # de un mismo sitio las consultas siguen siendo secuenciales, al mismo
    # ritmo de siempre — a ningún sitio le llega más tráfico por segundo del
    # que ya le llegaba, así que esto no aumenta el riesgo de bloqueo anti-bot.
    grupos = {}
    for _, prod in productos.iterrows():
        grupos.setdefault(prod["retailer"], []).append(prod)

    tareas_resultado = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(grupos))) as executor:
        futuros = []
        for retailer_key, lista_productos in grupos.items():
            cfg = retailers_cfg.get(retailer_key)
            if cfg is None:
                for prod in lista_productos:
                    tareas_resultado.append((prod, None, None, False, f"Retailer '{retailer_key}' no está en retailers.yaml"))
                continue
            if cfg["metodo"] in ("playwright", "playwright_text", "playwright_json"):
                futuros.append(executor.submit(_procesar_lote_playwright, cfg, lista_productos))
            else:
                futuros.append(executor.submit(_procesar_lote_http, cfg, lista_productos))

        for futuro in concurrent.futures.as_completed(futuros):
            tareas_resultado.extend(futuro.result())

    resultados = []
    for prod, precio, precio_normal, disponible, error in tareas_resultado:
        retailer_key = prod["retailer"]
        cfg = retailers_cfg.get(retailer_key)
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

    resultados.sort(key=lambda r: r["sku_interno"])

    # La marca de tiempo se calcula acá adentro (no afuera, en la función que
    # renderiza) para que quede guardada junto con el resto de datos en el
    # caché compartido: todos los usuarios ven la hora en que se hizo la
    # consulta real, no la hora en que cada uno abrió la página.
    momento_actualizacion = datetime.now(ZONA_HORARIA_CL).strftime("%d/%m/%Y %H:%M hrs")
    return resultados, momento_actualizacion


def agrupar_por_formato_ovella(datos, ovella_df):
    """Junta cada SKU de Ovella con los competidores del MISMO metraje total
    (ej. 'Doble Hoja 50m 4un' = 200m agrupa a cualquier competidor que también
    sea 200m, sin importar marca/retailer). Esto es lo que realmente responde
    la pregunta del negocio ('quién compite con este SKU'), en vez de una
    tabla plana con todo mezclado. Devuelve (grupos, sin_match):
      - grupos: uno por fila de ovella.csv, con sus competidores ordenados por $/metro.
      - sin_match: competidores cuyo metraje no coincide con ningún SKU Ovella."""
    validos = [d for d in datos if d["precio_metro_num"] > 0]
    usados = set()
    grupos = []

    for _, ov in ovella_df.iterrows():
        competidores = [d for d in datos if d["metros_totales"] == ov["metros_totales"]]
        competidores_ordenados = sorted(
            competidores, key=lambda d: (d["precio_metro_num"] <= 0, d["precio_metro_num"])
        )
        usados.update(id(d) for d in competidores)
        grupos.append({"ovella": ov, "competidores": competidores_ordenados})

    sin_match = [d for d in datos if id(d) not in usados]

    return grupos, sin_match


def _formatear_clp(valor):
    return f"${valor:,.0f}".replace(",", ".")


def _tabla_competidores(competidores):
    """Arma el DataFrame de competidores de un grupo, con la fila más barata
    resaltada (verde translúcido), las sin stock/con error en rojo
    translúcido — colores de status validados, nunca usados para series — y
    separando precio de lista vs. precio de oferta cuando difieren, para que
    se note quién está con promoción activa y quién no."""
    filas = []
    for c in competidores:
        normal = c["precio_normal_num"]
        oferta = c["precio_oferta_num"]
        en_oferta = bool(oferta and normal and oferta < normal)
        descuento_pct = round((1 - oferta / normal) * 100) if en_oferta else None
        filas.append({
            "Retailer": c["retailer_nombre"],
            "Marca": c["marca"],
            "Producto": c["producto"],
            "Precio Lista": _formatear_clp(normal) if normal else "N/D",
            "Precio Oferta": _formatear_clp(oferta) if en_oferta else "—",
            "Desc.": f"-{descuento_pct}%" if en_oferta else "—",
            "$/Metro": c["precio_metro_num"] if c["precio_metro_num"] else None,
            "Estado": c["estado"],
        })
    df = pd.DataFrame(filas)

    precios_validos = df["$/Metro"].dropna()
    minimo = precios_validos.min() if not precios_validos.empty else None

    def resaltar_fila(fila):
        if fila["$/Metro"] == minimo and minimo is not None:
            return [f"background-color: {COLOR_BUENO}26"] * len(fila)
        if fila["Estado"] not in ("Disponible",):
            return [f"background-color: {COLOR_CRITICO}1a"] * len(fila)
        return [""] * len(fila)

    return df.style.apply(resaltar_fila, axis=1).format({"$/Metro": lambda v: f"${v}/m" if pd.notna(v) else "N/D"})


# --- Interfaz ---
st.markdown(f"""
<style>
[data-testid="stMetricValue"] {{ font-size: 1.4rem; }}
h3 {{ color: {COLOR_ACENTO}; }}
</style>
""", unsafe_allow_html=True)

st.title("📊 Dipisa & Ovella — Monitor de Pricing en Vivo")
st.caption("Cada SKU de Ovella (ovella.csv) con su competencia real, agrupada por formato — sin tocar código para sumar productos (productos.csv) o tiendas (retailers.yaml)")

productos_df, _ = cargar_config()
ovella_df = cargar_ovella()
hash_productos = str(pd.util.hash_pandas_object(productos_df).sum())  # invalida cache si cambia el CSV
datos_tabla, momento_actualizacion = consultar_precios_en_vivo(hash_productos)
grupos, sin_match = agrupar_por_formato_ovella(datos_tabla, ovella_df)

col_info, col_boton = st.columns([4, 1])
with col_info:
    st.info(
        f"🕒 Último informe solicitado: **{momento_actualizacion}** — "
        "el mismo informe lo ve cualquiera que entre a la página hasta que alguien lo actualice."
    )
with col_boton:
    if st.button("🔄 Forzar recarga", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

con_datos = [d for d in datos_tabla if d["precio_metro_num"] > 0]
sin_stock = [d for d in datos_tabla if d["estado"] == "❌ Sin Stock"]
con_error = [d for d in datos_tabla if d["precio_oferta_num"] == 0]

c1, c2, c3, c4 = st.columns(4)
c1.metric("SKU de Ovella", len(ovella_df))
c2.metric("SKU de competencia monitoreados", len(datos_tabla))
c3.metric("Sin stock detectado", len(sin_stock))
c4.metric("Con error de lectura", len(con_error))

st.divider()

for grupo in grupos:
    ov = grupo["ovella"]
    competidores = grupo["competidores"]
    with st.container(border=True):
        st.subheader(f"🧻 Ovella — {ov['producto']} ({int(ov['metros_totales'])} m totales)")

        if not competidores:
            st.caption("Todavía no hay competidores monitoreados con este mismo metraje.")
            continue

        validos = [c for c in competidores if c["precio_metro_num"] > 0]
        if validos:
            mas_barato = min(validos, key=lambda c: c["precio_metro_num"])
            mas_caro = max(validos, key=lambda c: c["precio_metro_num"])
            promedio = round(sum(c["precio_metro_num"] for c in validos) / len(validos), 1)

            m1, m2, m3 = st.columns(3)
            m1.metric("Más barato ($/m)", f"${mas_barato['precio_metro_num']}",
                      help=f"{mas_barato['marca']} — {mas_barato['retailer_nombre']}")
            m2.metric("Promedio ($/m)", f"${promedio}")
            m3.metric("Más caro ($/m)", f"${mas_caro['precio_metro_num']}",
                      help=f"{mas_caro['marca']} — {mas_caro['retailer_nombre']}")

        try:
            st.dataframe(_tabla_competidores(competidores), width="stretch", hide_index=True)
        except TypeError:
            st.dataframe(_tabla_competidores(competidores), use_container_width=True, hide_index=True)

if sin_match:
    with st.expander(f"📦 Otros {len(sin_match)} productos monitoreados (sin formato Ovella equivalente)"):
        st.caption("Mismo metraje que ningún SKU en ovella.csv — agrégalo ahí si corresponde a un formato propio.")
        try:
            st.dataframe(_tabla_competidores(sin_match), width="stretch", hide_index=True)
        except TypeError:
            st.dataframe(_tabla_competidores(sin_match), use_container_width=True, hide_index=True)
