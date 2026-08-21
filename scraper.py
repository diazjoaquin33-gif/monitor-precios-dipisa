import pandas as pd
import yaml
import requests
import json
import re
import time
import concurrent.futures
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from curl_cffi import requests as cffi_requests

BASE_DIR = Path(__file__).parent
PRODUCTOS_PATH = BASE_DIR / "productos.csv"
RETAILERS_PATH = BASE_DIR / "retailers.yaml"
DATOS_PATH = BASE_DIR / "datos_procesados.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def cargar_config():
    productos = pd.read_csv(PRODUCTOS_PATH, comment="#", skip_blank_lines=True)
    productos = productos.dropna(subset=["sku_interno"])
    with open(RETAILERS_PATH, "r", encoding="utf-8") as f:
        retailers = yaml.safe_load(f)
    return productos, retailers

def _consultar_lider_api(url: str):
    m_id = re.search(r'/(\d{8,16})(?:\?|$)', url)
    if not m_id:
        m_id = re.search(r'(\d+)', url.rstrip("/").split("/")[-1])
    if not m_id: return None, None, False, "Sin ID"
    sku_raw = m_id.group(1)
    
    headers_mobile = {
        "User-Agent": "LiderApp/4.26.0 (iPhone; iOS 17.4; Scale/3.00)",
        "Accept": "application/json",
        "x-channel": "MOBILE_APP",
    }
    
    try:
        res = cffi_requests.get(f"https://svcs.lider.cl/orchestration/catalog/products/{sku_raw}", headers=headers_mobile, impersonate="safari15_5", timeout=10)
        if res.status_code == 200:
            data = res.json()
            if "price" in data:
                p_oferta = data["price"].get("OfferPrice") or data["price"].get("BasePriceReference")
                p_normal = data["price"].get("BasePriceReference") or p_oferta
                if p_oferta: return float(p_oferta), float(p_normal or p_oferta), True, None
    except Exception: pass
    return None, None, False, "Error Líder"

def _consultar_curl_cffi(url: str, cfg: dict):
    try:
        res = cffi_requests.get(url, headers=HEADERS, impersonate="chrome124", timeout=10)
        if res.status_code == 200:
            texto = res.text
            precio_oferta = None
            precio_normal = None

            if "tottus.cl" in url:
                m_tottus = re.search(r'"@type"\s*:\s*"Product".*?"price"\s*:\s*"?(\d+(?:\.\d+)?)"?', texto, re.DOTALL)
                if m_tottus: precio_oferta = float(m_tottus.group(1).replace(".", ""))
                if not precio_oferta:
                    m_p = re.search(r'"(?:currentPrice|offerPrice|unitPrice|salePrice)"\s*:\s*(\d+)', texto)
                    if m_p: precio_oferta = float(m_p.group(1))
                m_norm = re.search(r'"(?:listPrice|originalPrice|normalPrice)"\s*:\s*(\d+)', texto)
                if m_norm: precio_normal = float(m_norm.group(1))

            if not precio_oferta and cfg.get("patron_precio_oferta"):
                m_oferta = re.search(cfg["patron_precio_oferta"], texto)
                if m_oferta: precio_oferta = float(m_oferta.group(1).replace(".", ""))

            if not precio_oferta:
                m_meta = re.search(r'(?:property|name)="product:price:amount"\s+content="([\d.,]+)"', texto)
                if m_meta: precio_oferta = float(m_meta.group(1).replace(".", "").replace(",", "."))

            return precio_oferta, precio_normal or precio_oferta, True, None
    except Exception: pass
    return None, None, False, "Error general"

def procesar_lote(retailer_key, lista_productos, cfg):
    salida = []
    for prod in lista_productos:
        metodo = cfg.get("metodo")
        if metodo == "lider_api":
            precio, precio_normal, disp, err = _consultar_lider_api(prod["url"])
        else:
            precio, precio_normal, disp, err = _consultar_curl_cffi(prod["url"], cfg)
        salida.append((prod["sku_interno"], precio, precio_normal, disp, err))
        time.sleep(0.5)
    return salida

if __name__ == "__main__":
    print("Iniciando extracción de precios...")
    productos, retailers_cfg = cargar_config()
    grupos = {}
    for _, prod in productos.iterrows():
        grupos.setdefault(prod["retailer"], []).append(prod)

    resultados_actuales = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futuros = [executor.submit(procesar_lote, k, v, retailers_cfg.get(k, {})) for k, v in grupos.items()]
        for futuro in concurrent.futures.as_completed(futuros):
            for sku, p, pn, disp, err in futuro.result():
                if p and p > 0:
                    resultados_actuales[sku] = {
                        "sku_interno": sku, "precio": p, "precio_normal": pn,
                        "estado": "Disponible", "error": None
                    }

    # Leer histórico para no perder precios de páginas caídas
    historico = []
    if DATOS_PATH.exists():
        with open(DATOS_PATH, "r", encoding="utf-8") as f:
            historico = json.load(f)
    
    datos_finales = []
    fecha_hoy = datetime.now(ZoneInfo("America/Santiago")).strftime("%d/%m/%Y %H:%M hrs")
    
    # Cruzar histórico con actuales
    hist_dict = {item["sku_interno"]: item for item in historico}
    for _, prod in productos.iterrows():
        sku = prod["sku_interno"]
        if sku in resultados_actuales:
            res = resultados_actuales[sku]
            res["fecha_act"] = fecha_hoy
            datos_finales.append(res)
        elif sku in hist_dict:
            viejo = hist_dict[sku]
            viejo["estado"] = f"⚠️ Últ. precio ({viejo.get('fecha_act', 'Desconocida').split()[0]})"
            datos_finales.append(viejo)
            
    with open(DATOS_PATH, "w", encoding="utf-8") as f:
        json.dump(datos_finales, f, ensure_ascii=False, indent=2)
    print("✅ Precios guardados exitosamente en datos_procesados.json")
