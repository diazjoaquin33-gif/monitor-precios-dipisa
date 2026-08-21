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

HEADERS_GENERICOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def cargar_config():
    productos = pd.read_csv(PRODUCTOS_PATH, comment="#", skip_blank_lines=True).dropna(subset=["sku_interno"])
    with open(RETAILERS_PATH, "r", encoding="utf-8") as f:
        retailers = yaml.safe_load(f)
    return productos, retailers

def _consultar_lider_api(url: str):
    m_id = re.search(r'/(\d{8,16})(?:\?|$)', url)
    if not m_id: m_id = re.search(r'(\d+)', url.rstrip("/").split("/")[-1])
    if not m_id: return None, None, False, "No se encontró ID en URL"
    sku_raw = m_id.group(1)
    
    headers_mobile = {"User-Agent": "LiderApp/4.26.0 (iPhone; iOS 17.4)", "Accept": "application/json", "x-channel": "MOBILE_APP"}
    try:
        res = cffi_requests.get(f"https://svcs.lider.cl/orchestration/catalog/products/{sku_raw}", headers=headers_mobile, impersonate="safari15_5", timeout=10)
        if res.status_code == 200:
            data = res.json()
            if "price" in data:
                p_oferta = data["price"].get("OfferPrice") or data["price"].get("BasePriceReference")
                p_normal = data["price"].get("BasePriceReference") or p_oferta
                if p_oferta: return float(p_oferta), float(p_normal or p_oferta), True, None
        else:
            return None, None, False, f"API SVCS devolvió HTTP {res.status_code}"
    except Exception as e: 
        return None, None, False, f"Error de red SVCS: {str(e)[:40]}"

    try:
        edge_url = f"https://api.allorigins.win/raw?url=https://bff.lider.cl/catalog/product/{sku_raw}"
        res = requests.get(edge_url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            p_oferta = data.get("price") or data.get("salePrice") or data.get("basePrice")
            p_normal = data.get("originalPrice") or data.get("listPrice")
            if p_oferta: return float(p_oferta), float(p_normal or p_oferta), True, None
        else:
            return None, None, False, f"Proxy Edge devolvió HTTP {res.status_code}"
    except Exception as e: 
        return None, None, False, f"Error de red Edge: {str(e)[:40]}"

    return None, None, False, "Bloqueo total Líder en ambas capas"

def _consultar_curl_cffi(url: str, cfg: dict):
    for intento in range(3): 
        impersonate_profile = ["chrome124", "safari15_5", "chrome120"][intento]
        try:
            res = cffi_requests.get(url, headers=HEADERS_GENERICOS, impersonate=impersonate_profile, timeout=12)
            if res.status_code == 200:
                texto = res.text
                precio_oferta, precio_normal = None, None

                if "tottus.cl" in url:
                    m_ld = re.search(r'"@type"\s*:\s*"Product".*?"price"\s*:\s*"?(\d+(?:\.\d+)?)"?', texto, re.DOTALL)
                    if m_ld: precio_oferta = float(m_ld.group(1).replace(".", ""))
                    if not precio_oferta:
                        m_json = re.search(r'"(?:currentPrice|offerPrice|salePrice)"\s*:\s*(\d+)', texto)
                        if m_json: precio_oferta = float(m_json.group(1))
                    m_norm = re.search(r'"(?:listPrice|originalPrice|normalPrice)"\s*:\s*(\d+)', texto)
                    if m_norm: precio_normal = float(m_norm.group(1))

                if not precio_oferta and cfg.get("patron_precio_oferta"):
                    m_oferta = re.search(cfg["patron_precio_oferta"], texto)
                    if m_oferta: precio_oferta = float(m_oferta.group(1).replace(".", "").replace(",", "."))

                if not precio_oferta:
                    m_meta = re.search(r'(?:property|name)="product:price:amount"\s+content="([\d.,]+)"', texto)
                    if m_meta: precio_oferta = float(m_meta.group(1).replace(".", "").replace(",", "."))

                if precio_oferta:
                    return precio_oferta, precio_normal or precio_oferta, True, None
                
                if intento == 2:
                    return None, None, False, "Regex no encontró el precio en el HTML"
            else:
                if intento == 2:
                    return None, None, False, f"HTTP {res.status_code}"
        except Exception as e:
            if intento == 2:
                return None, None, False, f"Timeout o Error CFFI: {str(e)[:40]}"
            time.sleep(1)
            continue
            
    return None, None, False, "Falla desconocida"

def _consultar_api_sisa(url: str, cfg: dict):
    slug = [p for p in url.rstrip("/").split("/") if p][-2 if "/p" in url else -1]
    body = {"store": "pedrofontova", "slug": slug}
    headers = {"apikey": "be-reg-groceries-sisa-catalog-wdhhq5a2fken", "Content-Type": "application/json"}
    try:
        res = requests.post("https://bff.santaisabel.cl/catalog/pdp", headers=headers, json=body, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if "items" in data and len(data["items"]) > 0:
                p_oferta = data["items"][0].get("price")
                p_normal = data["items"][0].get("listPrice")
                if p_oferta: return float(p_oferta), float(p_normal or p_oferta), True, None
        return None, None, False, f"API devolvió HTTP {res.status_code}"
    except Exception as e: 
        return None, None, False, f"Error conexión API: {str(e)[:40]}"

def procesar_lote(retailer_key, lista_productos, cfg):
    salida = []
    for prod in lista_productos:
        metodo = cfg.get("metodo")
        if metodo == "lider_api":
            p, pn, disp, err = _consultar_lider_api(prod["url"])
        elif metodo == "api_post_json":
            p, pn, disp, err = _consultar_api_sisa(prod["url"], cfg)
        else:
            p, pn, disp, err = _consultar_curl_cffi(prod["url"], cfg)
        
        # --- EL MODO CHISMOSO: Imprimir resultados en vivo ---
        if p:
            print(f"✅ {retailer_key} | {prod['sku_interno']} -> Extraído correctamente: ${p}")
        else:
            print(f"❌ {retailer_key} | {prod['sku_interno']} -> FALLÓ: {err}")
            
        salida.append((prod["sku_interno"], p, pn, disp, err))
    return salida

if __name__ == "__main__":
    print("🤖 Iniciando motor blindado de extracción de precios...")
    productos, retailers_cfg = cargar_config()
    grupos = {}
    for _, prod in productos.iterrows():
        grupos.setdefault(prod["retailer"], []).append(prod)

    resultados_actuales = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futuros = [executor.submit(procesar_lote, k, v, retailers_cfg.get(k, {})) for k, v in grupos.items()]
        for futuro in concurrent.futures.as_completed(futuros):
            for sku, p, pn, disp, err in futuro.result():
                if p and p > 0:
                    resultados_actuales[sku] = {
                        "sku_interno": sku, "precio": p, "precio_normal": pn,
                        "estado": "Disponible", "error": None
                    }

    historico = []
    if DATOS_PATH.exists():
        try:
            with open(DATOS_PATH, "r", encoding="utf-8") as f:
                historico = json.load(f)
        except json.JSONDecodeError: pass
    
    datos_finales = []
    fecha_hoy = datetime.now(ZoneInfo("America/Santiago")).strftime("%d/%m/%Y %H:%M hrs")
    
    hist_dict = {item["sku_interno"]: item for item in historico}
    for _, prod in productos.iterrows():
        sku = prod["sku_interno"]
        if sku in resultados_actuales:
            res = resultados_actuales[sku]
            res["fecha_act"] = fecha_hoy
            datos_finales.append(res)
        elif sku in hist_dict:
            viejo = hist_dict[sku]
            if "fecha_act" in viejo:
                viejo["estado"] = f"⚠️ Últ. precio ({viejo['fecha_act'].split()[0]})"
            datos_finales.append(viejo)
            
    with open(DATOS_PATH, "w", encoding="utf-8") as f:
        json.dump(datos_finales, f, ensure_ascii=False, indent=2)
    print("🏁 Extracción terminada. JSON actualizado.")
