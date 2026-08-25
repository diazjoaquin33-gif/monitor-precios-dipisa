import pandas as pd
import yaml
import requests
import json
import re
import time
import random
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
    "Accept": "text/html,application/xhtml+xml,application/json,application/xml;q=0.9,*/*;q=0.8",
}

def cargar_config():
    productos = pd.read_csv(PRODUCTOS_PATH, comment="#", skip_blank_lines=True).dropna(subset=["sku_interno"])
    with open(RETAILERS_PATH, "r", encoding="utf-8") as f:
        retailers = yaml.safe_load(f)
    return productos, retailers

INSTALEAP_QUERY = (
    "fragment CategoryFields on CategoryModel {\n  active\n  boost\n  hasChildren\n  categoryNamesPath\n  isAvailableInHome\n  level\n  name\n  path\n  reference\n  slug\n  photoUrl\n  imageUrl\n  shortName\n  isFeatured\n  isAssociatedToCatalog\n  __typename\n}\n\n"
    "fragment CatalogProductTagModel on CatalogProductTagModel {\n  description\n  enabled\n  textColor\n  filter\n  tagReference\n  backgroundColor\n  name\n  __typename\n}\n\n"
    "fragment CatalogProductFormatModel on CatalogProductFormatModel {\n  format\n  equivalence\n  unitEquivalence\n  clickMultiplier\n  minQty\n  maxQty\n  __typename\n}\n\n"
    "fragment Taxes on ProductTaxModel {\n  taxId\n  taxName\n  taxType\n  taxValue\n  taxSubTotal\n  __typename\n}\n\n"
    "fragment PromotionCondition on PromotionCondition {\n  quantity\n  price\n  priceBeforeTaxes\n  taxTotal\n  taxes {\n    ...Taxes\n    __typename\n  }\n  __typename\n}\n\n"
    "fragment Promotion on Promotion {\n  type\n  isActive\n  conditions {\n    ...PromotionCondition\n    __typename\n  }\n  description\n  endDateTime\n  startDateTime\n  __typename\n}\n\n"
    "fragment PromotedModel on PromotedModel {\n  isPromoted\n  onLoadBeacon\n  onClickBeacon\n  onViewBeacon\n  onBasketChangeBeacon\n  onWishlistBeacon\n  __typename\n}\n\n"
    "fragment SpecificationModel on SpecificationModel {\n  title\n  values {\n    label\n    value\n    __typename\n  }\n  __typename\n}\n\n"
    "fragment NutritionalDetailsInformation on NutritionalDetailsInformation {\n  servingName\n  servingSize\n  servingUnit\n  servingsPerPortion\n  nutritionalTable {\n    nutrientName\n    quantity\n    unit\n    quantityPerPortion\n    dailyValue\n    __typename\n  }\n  bottomInfo\n  __typename\n}\n\n"
    "fragment Promotions on PromotionV2 {\n  type\n  description\n  promotionReference\n  startDateTime\n  endDateTime\n  isActive\n  conditions {\n    field\n    operator\n    values\n    value\n    __typename\n  }\n  restrictions {\n    field\n    operator\n    value\n    __typename\n  }\n  benefit {\n    type\n    label\n    value\n    values\n    imagesURL\n    qty\n    __typename\n  }\n  __typename\n}\n\n"
    "fragment CatalogProductModel on CatalogProductModel {\n  name\n  price\n  photosUrl\n  unit\n  subUnit\n  subQty\n  description\n  sku\n  ean\n  maxQty\n  minQty\n  clickMultiplier\n  nutritionalDetails\n  isActive\n  slug\n  brand\n  stock\n  securityStock\n  boost\n  isAvailable\n  location\n  priceBeforeTaxes\n  taxTotal\n  allowSubstitutions\n  promotion {\n    ...Promotion\n    __typename\n  }\n  taxes {\n    ...Taxes\n    __typename\n  }\n  categories {\n    ...CategoryFields\n    __typename\n  }\n  categoriesData {\n    ...CategoryFields\n    __typename\n  }\n  formats {\n    ...CatalogProductFormatModel\n    __typename\n  }\n  tags {\n    ...CatalogProductTagModel\n    __typename\n  }\n  specifications {\n    ...SpecificationModel\n    __typename\n  }\n  promoted {\n    ...PromotedModel\n    __typename\n  }\n  score\n  relatedProducts\n  ingredients\n  stockWarning\n  nutritionalDetailsInformation {\n    ...NutritionalDetailsInformation\n    __typename\n  }\n  productVariants\n  isVariant\n  isDominant\n  promotions {\n    ...Promotions\n    __typename\n  }\n  seals\n  previousPrice\n  previousPricePerSubUnit\n  promotionPricePerSubUnit\n  pricePerSubUnit\n  hasAgeRestriction\n  type\n  __typename\n}\n\n"
    "query GetProductsBySKU($getProductsBySkuInput: GetProductsBySKUInput!) {\n  getProductsBySKU(getProductsBySKUInput: $getProductsBySkuInput) {\n    ...CatalogProductModel\n    __typename\n  }\n}"
)


def _consultar_instaleap(url: str, cfg: dict):
    """Varios supermercados chilenos (ej. aCuenta) usan Instaleap como backend
    de catálogo — la ficha de producto es una SPA que renderiza el precio con
    JS, pero el propio frontend lo pide a esta API GraphQL pública, sin
    protección anti-bot ni problema de certificado (a diferencia del dominio
    principal del retailer). El SKU se saca del último tramo de la URL
    (todas las fichas terminan en "-{sku}")."""
    sku = url.rstrip("/").split("-")[-1]
    payload = [{
        "operationName": "GetProductsBySKU",
        "variables": {"getProductsBySkuInput": {
            "clientId": cfg["client_id"],
            "skus": [sku],
            "storeReference": cfg["store_reference"],
        }},
        "query": INSTALEAP_QUERY,
    }]
    try:
        res = requests.post(
            "https://nextgentheadless.instaleap.io/api/v3",
            json=payload, headers=HEADERS_GENERICOS, timeout=12,
        )
        if res.status_code != 200:
            return None, None, False, f"HTTP {res.status_code}"
        data = res.json()
        productos_resp = data[0]["data"]["getProductsBySKU"]
        if not productos_resp:
            return None, None, False, f"SKU '{sku}' no encontrado en Instaleap"
        p = productos_resp[0]
        precio = p.get("price")
        if not precio:
            return None, None, False, "Sin precio en la respuesta"
        precio_normal = p.get("previousPrice") or precio
        disponible = bool(p.get("isAvailable")) and (p.get("stock") or 0) > 0
        return float(precio), float(precio_normal), disponible, None
    except Exception as e:
        return None, None, False, f"Error Instaleap: {str(e)[:80]}"


def _consultar_lider_api(url: str):
    m_id = re.search(r'/(\d{8,16})(?:\?|$)', url)
    if not m_id: m_id = re.search(r'(\d+)', url.rstrip("/").split("/")[-1])
    if not m_id: return None, None, False, "No se encontró ID en URL"
    sku_raw = m_id.group(1)
    sku_limpio = sku_raw.lstrip("0") or sku_raw
    
    try:
        graphql_url = "https://www.lider.cl/graphql"
        payload = {
            "operationName": "GetProductById",
            "variables": {"productId": sku_limpio},
            "query": "query GetProductById($productId: String!) { product(id: $productId) { price { offerPrice basePrice } } }"
        }
        headers_gql = {"User-Agent": HEADERS_GENERICOS["User-Agent"], "Content-Type": "application/json", "x-channel": "WEB"}
        res = cffi_requests.post(graphql_url, headers=headers_gql, json=payload, impersonate="chrome124", timeout=10)
        if res.status_code == 200:
            data = res.json()
            p_info = data.get("data", {}).get("product", {}).get("price", {})
            if p_info:
                p_oferta = p_info.get("offerPrice") or p_info.get("basePrice")
                p_normal = p_info.get("basePrice") or p_oferta
                if p_oferta: return float(p_oferta), float(p_normal), True, None
    except Exception: 
        pass

    try:
        edge_url = f"https://api.allorigins.win/raw?url=https://bff.lider.cl/catalog/product/{sku_raw}"
        res = requests.get(edge_url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            p_oferta = data.get("price") or data.get("salePrice") or data.get("basePrice")
            p_normal = data.get("originalPrice") or data.get("listPrice")
            if p_oferta: return float(p_oferta), float(p_normal or p_oferta), True, None
    except Exception: 
        pass

    return None, None, False, "Bloqueo total Líder"

def _consultar_curl_cffi(url: str, cfg: dict):
    for intento in range(3): 
        impersonate_profile = ["chrome124", "safari15_5", "chrome120"][intento]
        try:
            res = cffi_requests.get(url, headers=HEADERS_GENERICOS, impersonate=impersonate_profile, timeout=12)
            if res.status_code == 200:
                texto = res.text
                precio_oferta, precio_normal = None, None

                # 1. FRANCOTIRADOR TOTTUS / FALABELLA
                if "tottus" in url or "falabella" in url:
                    m_event = re.search(r'"type"\s*:\s*"eventPrice".*?"price"\s*:\s*\[\s*"?([\d.]+)"?\s*\]', texto, re.DOTALL | re.IGNORECASE)
                    m_normal = re.search(r'"type"\s*:\s*"normalPrice".*?"price"\s*:\s*\[\s*"?([\d.]+)"?\s*\]', texto, re.DOTALL | re.IGNORECASE)
                    
                    if m_event:
                        precio_oferta = float(m_event.group(1).replace(".", ""))
                        if m_normal:
                            precio_normal = float(m_normal.group(1).replace(".", ""))
                    
                    if not precio_oferta:
                        m_prices = re.findall(r'"price"\s*:\s*\[\s*"?(\d+(?:\.\d+)?)"?\s*\]', texto, re.IGNORECASE)
                        if m_prices:
                            precios = [float(p.replace(".", "")) for p in m_prices if float(p.replace(".", "")) > 100]
                            if precios:
                                precio_oferta = min(precios)
                                precio_normal = max(precios)

                # 2. FRANCOTIRADOR CENCOSUD (Jumbo y Santa Isabel)
                if not precio_oferta and ("jumbo" in url or "santaisabel" in url):
                    # 2A) Anclaje SEO: El precio real de este producto exacto (ignora los relacionados)
                    m_meta = re.search(r'(?:property|name)="(?:product:price:amount|og:price:amount)"\s+content="([\d.,]+)"', texto)
                    if not m_meta:
                        # VTEX no publica product:price:amount cuando el producto está
                        # agotado (no hay "oferta" que describir) — no es una falla de
                        # scraping, es el estado real del producto. Se corta acá mismo
                        # (sin gastar los 3 reintentos con distintos perfiles) y se
                        # etiqueta como "Sin stock" en vez del genérico "no encontrado".
                        m_disp = re.search(r'(?:property|name)="product:availability"\s+content="([^"]+)"', texto)
                        if m_disp and "out of stock" in m_disp.group(1).lower():
                            return None, None, False, "Sin stock"
                    if m_meta:
                        precio_oferta = float(m_meta.group(1).replace(".", "").replace(",", "."))

                        # 2B) listPrice embebido en el bloque de hidratación: NO es un objeto
                        # {} aislado (viene suelto entre otros campos de un objeto más grande
                        # con arrays/objetos anidados alrededor, así que buscar un bloque {}
                        # sin llaves internas nunca calzaba con la estructura real). Se busca
                        # directo "price":X,"listPrice":Y anclado al precio ya confirmado por
                        # el meta tag, para no traer el descuento de un producto relacionado.
                        m_lista = re.search(
                            rf'\\?"price\\?":{int(precio_oferta)},\\?"listPrice\\?":([\d.]+)',
                            texto, re.IGNORECASE,
                        )
                        if m_lista:
                            list_val = float(m_lista.group(1))
                            precio_normal = list_val if list_val > precio_oferta else precio_oferta

                        if not precio_normal:
                            precio_normal = precio_oferta

                # 3. Búsqueda YAML genérica
                if not precio_oferta and cfg.get("patron_precio_oferta"):
                    m_oferta = re.search(cfg.get("patron_precio_oferta"), texto, re.DOTALL)
                    if m_oferta: precio_oferta = float(m_oferta.group(1).replace(".", "").replace(",", "."))
                if not precio_normal and cfg.get("patron_precio_normal"):
                    m_normal = re.search(cfg.get("patron_precio_normal"), texto, re.DOTALL)
                    if m_normal: precio_normal = float(m_normal.group(1).replace(".", "").replace(",", "."))

                if precio_oferta:
                    if not precio_normal or precio_normal <= precio_oferta:
                        precio_normal = precio_oferta
                    return precio_oferta, precio_normal, True, None
                
                if intento == 2: return None, None, False, "No se encontró el precio en el HTML"
            else:
                if intento == 2: return None, None, False, f"HTTP {res.status_code}"
        except Exception as e:
            if intento == 2: return None, None, False, f"Error CFFI: {str(e)[:40]}"
            time.sleep(1)
            continue
    return None, None, False, "Falla desconocida"

def procesar_lote(retailer_key, lista_productos, cfg):
    salida = []
    for prod in lista_productos:
        metodo = cfg.get("metodo")
        if metodo == "lider_api":
            p, pn, disp, err = _consultar_lider_api(prod["url"])
        elif metodo == "instaleap":
            p, pn, disp, err = _consultar_instaleap(prod["url"], cfg)
        elif metodo == "api_post_json":
            p, pn, disp, err = _consultar_curl_cffi(prod["url"], cfg)
        else:
            p, pn, disp, err = _consultar_curl_cffi(prod["url"], cfg)
        
        if p:
            if pn and p < pn:
                print(f"✅ {retailer_key} | {prod['sku_interno']} -> OFERTA: ${p} (Normal: ${pn})")
            else:
                print(f"✅ {retailer_key} | {prod['sku_interno']} -> Extraído: ${p}")
        else:
            print(f"❌ {retailer_key} | {prod['sku_interno']} -> FALLÓ: {err}")
            
        salida.append((prod["sku_interno"], p, pn, disp, err))
        # Espacio entre productos del MISMO retailer — sin esto, varios
        # productos seguidos del mismo sitio uno detrás de otro es justo el
        # patrón que hace escalar bloqueos tipo Cloudflare (ej. Tottus).
        time.sleep(random.uniform(0.8, 1.8))
    return salida

if __name__ == "__main__":
    print("🤖 Iniciando motor de extracción de precios (Modo Autónomo)...")
    
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
            with open(DATOS_PATH, "r", encoding="utf-8") as f: historico = json.load(f)
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
