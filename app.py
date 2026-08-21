import streamlit as st
import pandas as pd
import yaml
import requests
import json
import re
import time
import subprocess
import sys
import concurrent.futures
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# Librería de evasión TLS/Anti-Bot
from curl_cffi import requests as cffi_requests

ZONA_HORARIA_CL = ZoneInfo("America/Santiago")

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
HISTORICO_PATH = BASE_DIR / "historico_precios.json"


# --- GESTIÓN DE PERSISTENCIA HISTÓRICA ---

def cargar_historico() -> dict:
    if HISTORICO_PATH.exists():
        try:
            with open(HISTORICO_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def guardar_historico(historico: dict):
    try:
        with open(HISTORICO_PATH, "w", encoding="utf-8") as f:
            json.dump(historico, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


@st.cache_resource
def asegurar_chromium_instalado():
    """Instala el navegador headless de Playwright si se requiere."""
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
    productos = productos.dropna(subset=["sku_interno"])
    with open(RETAILERS_PATH, "r", encoding="utf-8") as f:
        retailers = yaml.safe_load(f)
    return productos, retailers


@st.cache_data(ttl=3600)
def cargar_ovella():
    ovella = pd.read_csv(OVELLA_PATH, comment="#", skip_blank_lines=True)
    return ovella.dropna(subset=["sku_ovella"])


def _fragmento_diagnostico(texto: str) -> str:
    limpio = re.sub(r"<[^>]+>", " ", texto)
    limpio = re.sub(r"\s+", " ", limpio).strip()
    return limpio[:180]


def _resolver_ruta(data, ruta):
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


# --- MÉTODOS HTTP CON AUTO-RECUPERACIÓN VTEX ---

def _consultar_vtex_por_id(retailer_base: str, product_id: str):
    try:
        api_url = f"{retailer_base}/api/catalog_system/pub/products/search?fq=productId:{product_id}"
        res = requests.get(api_url, headers=HEADERS, timeout=6)
        if res.status_code == 200:
            data = res.json()
            if data and isinstance(data, list) and len(data) > 0:
                offer = data[0]["items"][0]["sellers"][0]["commertialOffer"]
                precio = offer.get("Price")
                precio_normal = offer.get("ListPrice") or precio
                disponible = offer.get("AvailableQuantity", 0) > 0
                if precio:
                    return float(precio), float(precio_normal), disponible, None
    except Exception:
        pass
    return None, None, False, "No encontrado por ID"


def _consultar_meta_tag(url: str, patron_precio: str, patron_disp: str, buscar_lista_embebida: bool = False, intentos: int = 2):
    if not patron_precio:
        return None, None, False, "Falta selector_precio"

    for intento in range(1, intentos + 1):
        try:
            res = requests.get(url, headers=HEADERS, timeout=8)
            if res.status_code == 200:
                m = re.search(patron_precio, res.text)
                if not m:
                    frag = _fragmento_diagnostico(res.text)
                    return None, None, True, f"No se encontró el precio. Recibido: \"{frag}\""
                precio = float(m.group(1).replace(".", "").replace(",", "."))

                precio_normal = precio
                if buscar_lista_embebida and precio == int(precio):
                    m_lista = re.search(
                        rf'\\?"price\\?":{int(precio)},\\?"listPrice\\?":(\d+)', res.text
                    )
                    if m_lista:
                        precio_normal = float(m_lista.group(1))

                disponible = True
                if patron_disp:
                    m_disp = re.search(patron_disp, res.text)
                    if m_disp:
                        disponible = "in stock" in m_disp.group(1).lower()

                return precio, precio_normal, disponible, None
            elif res.status_code == 404:
                m_id = re.search(r'-(\d+)/p', url)
                if m_id:
                    base_url = "/".join(url.split("/")[:3])
                    p, pn, disp, err = _consultar_vtex_por_id(base_url, m_id.group(1))
                    if p:
                        return p, pn, disp, None
                return None, None, False, "URL expirada (HTTP 404)"
            elif res.status_code in (403, 429):
                time.sleep(1.0 * intento)
                continue
            else:
                return None, None, False, f"HTTP {res.status_code}"
        except requests.RequestException as e:
            if intento == intentos:
                return None, None, False, f"Error de conexión: {str(e)[:100]}"
            time.sleep(1.0 * intento)
    return None, None, False, "Bloqueado tras varios intentos"


def _consultar_text_pattern(url: str, cfg: dict, intentos: int = 2):
    patron_oferta = cfg.get("patron_precio_oferta")
    patron_normal = cfg.get("patron_precio_normal")

    if not patron_oferta:
        return None, None, False, "Falta patron_precio_oferta en config"

    for intento in range(1, intentos + 1):
        try:
            res = requests.get(url, headers=HEADERS, timeout=8)
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
            elif res.status_code == 404:
                return None, None, False, "URL expirada (HTTP 404)"
            elif res.status_code in (403, 429):
                time.sleep(1.0 * intento)
                continue
            else:
                return None, None, False, f"HTTP {res.status_code}"
        except requests.RequestException as e:
            if intento == intentos:
                return None, None, False, f"Error de conexión: {str(e)[:100]}"
            time.sleep(1.0 * intento)
    return None, None, False, "Bloqueado tras varios intentos"


def _consultar_api_post_json(url: str, cfg: dict, intentos: int = 2):
    partes = [p for p in url.rstrip("/").split("/") if p]
    slug = partes[-2] if partes and partes[-1] == "p" else (partes[-1] if partes else "")
    body = {**cfg.get("api_body", {}), "slug": slug}
    origen = "/".join(url.split("/")[:3])
    headers = {
        **HEADERS,
        **cfg.get("api_headers", {}),
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": url,
        "Origin": origen,
    }

    for intento in range(1, intentos + 1):
        try:
            res = requests.post(cfg["api_url"], headers=headers, json=body, timeout=8)
            if res.status_code == 200:
                data = res.json()
                precio = _resolver_ruta(data, cfg.get("campo_precio", ""))
                if precio is None:
                    if intento == intentos:
                        return None, None, True, f"El JSON no trajo '{cfg.get('campo_precio')}'"
                    time.sleep(1.0 * intento)
                    continue
                precio_normal = precio
                if cfg.get("campo_precio_normal"):
                    valor_normal = _resolver_ruta(data, cfg["campo_precio_normal"])
                    if valor_normal is not None:
                        precio_normal = valor_normal
                disponible = True
                if cfg.get("campo_disponible"):
                    valor_disp = _resolver_ruta(data, cfg["campo_disponible"])
                    if valor_disp is not None:
                        disponible = bool(valor_disp)
                return float(precio), float(precio_normal), disponible, None
            elif res.status_code == 404:
                return None, None, False, "Slug expirado (HTTP 404)"
            elif res.status_code in (403, 429):
                if intento == intentos:
                    return None, None, False, f"Bloqueado (HTTP {res.status_code})"
                time.sleep(1.5 * intento)
            else:
                return None, None, False, f"HTTP {res.status_code}"
        except requests.RequestException as e:
            if intento == intentos:
                return None, None, False, f"Error de conexión: {str(e)[:100]}"
            time.sleep(1.0 * intento)
    return None, None, False, "Falla desconocida"


# --- MÉTODOS ESPECIALIZADOS CURL_CFFI (LÍDER, TOTTUS, ALVI) ---

def _consultar_lider_api(url: str, intentos: int = 2):
    """Consulta endpoints de backend de Walmart Chile sin pasar por la barrera PerimeterX."""
    # Extrae el ID / SKU numérico de la URL
    m_id = re.search(r'/(\d{8,16})(?:\?|$)', url)
    if not m_id:
        m_id = re.search(r'(\d+)', url.rstrip("/").split("/")[-1])
    
    if not m_id:
        return None, None, False, "No se pudo extraer ID del producto"

    sku_raw = m_id.group(1)
    sku_limpio = sku_raw.lstrip("0") or sku_raw

    headers_api = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 LiderApp/4.26.0",
        "Accept": "application/json, text/plain, */*",
        "x-channel": "MOBILE_APP",
        "tenant": "supermercado",
    }

    # 1. Intento por API de orquestación de Walmart
    urls_orquestador = [
        f"https://svcs.lider.cl/orchestration/catalog/products/{sku_raw}",
        f"https://svcs.lider.cl/orchestration/catalog/products/{sku_limpio}",
        f"https://buysite.lider.cl/orchestration/catalog/products/{sku_raw}",
    ]

    for ep in urls_orquestador:
        try:
            res = cffi_requests.get(ep, headers=headers_api, impersonate="chrome124", timeout=6)
            if res.status_code == 200:
                data = res.json()
                # Extracción desde payload de producto
                p_oferta = None
                p_normal = None

                if "price" in data:
                    if isinstance(data["price"], dict):
                        p_oferta = data["price"].get("OfferPrice") or data["price"].get("BasePriceReference")
                        p_normal = data["price"].get("BasePriceReference") or p_oferta
                    else:
                        p_oferta = data["price"]

                if not p_oferta and "OfferPrice" in data:
                    p_oferta = data["OfferPrice"]
                    p_normal = data.get("BasePriceReference", p_oferta)

                if p_oferta:
                    return float(p_oferta), float(p_normal or p_oferta), True, None
        except Exception:
            continue

    # 2. Intento por Search Query interno (Resuelve el producto por su SKU)
    try:
        search_url = f"https://svcs.lider.cl/orchestration/catalog/categories/products?query={sku_limpio}&page=1&elementsPerPage=1"
        res = cffi_requests.get(search_url, headers=headers_api, impersonate="chrome124", timeout=6)
        if res.status_code == 200:
            data = res.json()
            prods = data.get("products", [])
            if prods:
                p_info = prods[0].get("price", {})
                p_oferta = p_info.get("OfferPrice") or p_info.get("BasePriceReference")
                p_normal = p_info.get("BasePriceReference") or p_oferta
                if p_oferta:
                    return float(p_oferta), float(p_normal), True, None
    except Exception:
        pass

    return None, None, False, "PerimeterX activo en Líder (usando último precio si existe)"


def _consultar_curl_cffi(url: str, cfg: dict, intentos: int = 2):
    patron_oferta = cfg.get("patron_precio_oferta")
    patron_normal = cfg.get("patron_precio_normal")

    headers_navegador = {
        **HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    for intento in range(1, intentos + 1):
        try:
            res = cffi_requests.get(url, headers=headers_navegador, impersonate="chrome124", timeout=10)
            if res.status_code == 200:
                texto = res.text
                precio_oferta = None
                precio_normal = None

                # 1. Extractor especializado para Tottus (Falabella Platform / Schema / Next.js)
                if "tottus.cl" in url:
                    m_tottus = re.search(r'"@type"\s*:\s*"Product".*?"price"\s*:\s*"?(\d+(?:\.\d+)?)"?', texto, re.DOTALL)
                    if m_tottus:
                        precio_oferta = float(m_tottus.group(1).replace(".", "").replace(",", "."))
                    
                    if not precio_oferta:
                        m_p = re.search(r'"(?:currentPrice|offerPrice|unitPrice|salePrice)"\s*:\s*(\d+)', texto)
                        if m_p:
                            precio_oferta = float(m_p.group(1))

                    if not precio_oferta:
                        m_raw_price = re.search(r'"price"\s*:\s*\[?"\$?\s*([\d.]+)"?\]?', texto)
                        if m_raw_price:
                            precio_oferta = float(m_raw_price.group(1).replace(".", ""))

                    m_norm = re.search(r'"(?:listPrice|originalPrice|normalPrice)"\s*:\s*(\d+)', texto)
                    if m_norm:
                        precio_normal = float(m_norm.group(1))

                # 2. Extractor genérico con patrones configurados
                if not precio_oferta and patron_oferta:
                    m_oferta = re.search(patron_oferta, texto)
                    if m_oferta:
                        precio_oferta = float(m_oferta.group(1).replace(".", "").replace(",", ""))

                if not precio_oferta:
                    m_meta = re.search(r'(?:property|name)="product:price:amount"\s+content="([\d.,]+)"', texto)
                    if m_meta:
                        precio_oferta = float(m_meta.group(1).replace(".", "").replace(",", "."))

                if not precio_oferta:
                    frag = _fragmento_diagnostico(texto)
                    if intento == intentos:
                        return None, None, True, f"No se encontró el precio. Recibido: \"{frag}\""
                    time.sleep(1.0 * intento)
                    continue

                if not precio_normal and patron_normal:
                    m_normal = re.search(patron_normal, texto)
                    if m_normal:
                        precio_normal = float(m_normal.group(1).replace(".", "").replace(",", ""))

                return precio_oferta, precio_normal or precio_oferta, True, None

            elif res.status_code == 404:
                return None, None, False, "URL expirada (HTTP 404)"
            elif res.status_code in (403, 429):
                time.sleep(1.0 * intento)
                continue
            else:
                return None, None, False, f"HTTP {res.status_code}"
        except Exception as e:
            if intento == intentos:
                return None, None, False, f"Error cffi: {str(e)[:100]}"
            time.sleep(1.0 * intento)

    return None, None, False, "Bloqueado tras varios intentos"


def _consultar_cffi_json(url: str, cfg: dict, intentos: int = 2):
    slug = url.rstrip("/").split("/")[-1]
    api_url = cfg["api_base"].rstrip("/") + "/" + slug

    headers_api = {
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Referer": url,
        "Origin": "/".join(url.split("/")[:3]),
    }

    for intento in range(1, intentos + 1):
        try:
            res = cffi_requests.get(api_url, headers=headers_api, impersonate="chrome124", timeout=8)
            if res.status_code == 200:
                data = res.json()
                precio = _resolver_ruta(data, cfg.get("campo_precio", ""))
                if precio is None:
                    if intento == intentos:
                        return None, None, True, f"El JSON no trajo '{cfg.get('campo_precio')}'"
                    time.sleep(1.0 * intento)
                    continue
                precio_normal = precio
                if cfg.get("campo_precio_normal"):
                    valor_normal = _resolver_ruta(data, cfg["campo_precio_normal"])
                    if valor_normal is not None:
                        precio_normal = valor_normal
                return float(precio), float(precio_normal), True, None
            elif res.status_code == 404:
                return None, None, False, "Slug expirado (HTTP 404)"
            elif res.status_code in (403, 429):
                if intento == intentos:
                    return None, None, False, f"Bloqueado (HTTP {res.status_code})"
                time.sleep(1.0 * intento)
            else:
                return None, None, False, f"HTTP {res.status_code}"
        except Exception as e:
            if intento == intentos:
                return None, None, False, f"Error cffi_json: {str(e)[:100]}"
            time.sleep(1.0 * intento)

    return None, None, False, "Falla desconocida"


# --- PROCESADORES DE LOTES ---

def _procesar_lote_http(cfg, lista_productos):
    salida = []
    for prod in lista_productos:
        metodo = cfg.get("metodo", "meta_tag")
        if metodo == "meta_tag":
            precio, precio_normal, disponible, error = _consultar_meta_tag(
                prod["url"], cfg.get("selector_precio"), cfg.get("selector_disponibilidad"),
                buscar_lista_embebida=cfg.get("buscar_precio_lista_embebido", False),
            )
        elif metodo == "api_post_json":
            precio, precio_normal, disponible, error = _consultar_api_post_json(prod["url"], cfg)
            if precio is None and cfg.get("selector_precio"):
                precio, precio_normal, disponible, error = _consultar_meta_tag(
                    prod["url"], cfg.get("selector_precio"), cfg.get("selector_disponibilidad"),
                )
        else:
            precio, precio_normal, disponible, error = _consultar_text_pattern(prod["url"], cfg)
        salida.append((prod, precio, precio_normal, disponible, error))
        time.sleep(0.3)
    return salida


def _procesar_lote_cffi(cfg, lista_productos):
    salida = []
    for prod in lista_productos:
        metodo = cfg.get("metodo")
        if metodo == "lider_api":
            precio, precio_normal, disponible, error = _consultar_lider_api(prod["url"])
        elif metodo == "cffi_json":
            precio, precio_normal, disponible, error = _consultar_cffi_json(prod["url"], cfg)
        else:
            precio, precio_normal, disponible, error = _consultar_curl_cffi(prod["url"], cfg)
        salida.append((prod, precio, precio_normal, disponible, error))
        time.sleep(0.3)
    return salida


@st.cache_data(ttl=1800)
def consultar_precios_en_vivo(productos_hash: str):
    productos, retailers_cfg = cargar_config()
    historico = cargar_historico()
    fecha_actual_str = datetime.now(ZONA_HORARIA_CL).strftime("%d/%m/%Y")

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

            metodo = cfg.get("metodo", "meta_tag")
            if metodo in ("curl_cffi", "cffi", "cffi_json", "lider_api"):
                futuros.append(executor.submit(_procesar_lote_cffi, cfg, lista_productos))
            else:
                futuros.append(executor.submit(_procesar_lote_http, cfg, lista_productos))

        for futuro in concurrent.futures.as_completed(futuros):
            tareas_resultado.extend(futuro.result())

    resultados = []
    hubo_cambios_historico = False

    for prod, precio, precio_normal, disponible, error in tareas_resultado:
        retailer_key = prod["retailer"]
        sku_id = str(prod["sku_interno"])
        cfg = retailers_cfg.get(retailer_key)

        if precio and precio > 0:
            historico[sku_id] = {
                "precio": precio,
                "precio_normal": precio_normal or precio,
                "fecha": fecha_actual_str
            }
            hubo_cambios_historico = True

            precio_metro = round(precio / prod["metros_totales"], 1) if prod.get("metros_totales") else None
            estado = "Disponible" if disponible else "❌ Sin Stock"
            resultados.append({
                **prod.to_dict(),
                "retailer_nombre": cfg["nombre"] if cfg else retailer_key,
                "precio_normal_num": precio_normal or precio,
                "precio_oferta_num": precio,
                "precio_metro_num": precio_metro or 0,
                "estado": estado,
            })
        else:
            respaldo = historico.get(sku_id)
            if respaldo:
                precio_resp = respaldo["precio"]
                precio_norm_resp = respaldo.get("precio_normal", precio_resp)
                precio_metro = round(precio_resp / prod["metros_totales"], 1) if prod.get("metros_totales") else None
                resultados.append({
                    **prod.to_dict(),
                    "retailer_nombre": cfg["nombre"] if cfg else retailer_key,
                    "precio_normal_num": precio_norm_resp,
                    "precio_oferta_num": precio_resp,
                    "precio_metro_num": precio_metro or 0,
                    "estado": f"⚠️ Últ. precio ({respaldo['fecha']})",
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

    if hubo_cambios_historico:
        guardar_historico(historico)

    resultados.sort(key=lambda r: r["sku_interno"])
    momento_actualizacion = datetime.now(ZONA_HORARIA_CL).strftime("%d/%m/%Y %H:%M hrs")
    return resultados, momento_actualizacion


def agrupar_por_formato_ovella(datos, ovella_df):
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
        if str(fila["Estado"]).startswith("❌") or str(fila["Estado"]).startswith("Sin"):
            return [f"background-color: {COLOR_CRITICO}1a"] * len(fila)
        return [""] * len(fila)

    return df.style.apply(resaltar_fila, axis=1).format({"$/Metro": lambda v: f"${v}/m" if pd.notna(v) else "N/D"})


# --- INTERFAZ STREAMLIT ---
st.markdown(f"""
<style>
[data-testid="stMetricValue"] {{ font-size: 1.4rem; }}
h3 {{ color: {COLOR_ACENTO}; }}
</style>
""", unsafe_allow_html=True)

st.title("📊 Dipisa & Ovella — Monitor de Pricing en Vivo")
st.caption("Cada SKU de Ovella (ovella.csv) con su competencia real, agrupada por formato — blindado ante expiración de URLs")

productos_df, _ = cargar_config()
ovella_df = cargar_ovella()
hash_productos = str(pd.util.hash_pandas_object(productos_df).sum())
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
