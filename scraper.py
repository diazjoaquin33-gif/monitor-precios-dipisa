import pandas as pd
import yaml
import requests
import json
import io
import re
import time
import random
import shutil
import concurrent.futures
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from curl_cffi import requests as cffi_requests

BASE_DIR = Path(__file__).parent
PRODUCTOS_PATH = BASE_DIR / "productos.csv"
RETAILERS_PATH = BASE_DIR / "retailers.yaml"
DATOS_PATH = BASE_DIR / "datos_procesados.json"
HISTORIAL_PATH = BASE_DIR / "historial_precios.csv"
ESTADO_SCRAPER_PATH = BASE_DIR / "estado_scraper.json"
OVERRIDES_CACHE_PATH = BASE_DIR / "url_overrides_cache.json"
CATALOGO_CACHE_PATH = BASE_DIR / "catalogo_cache.csv"
RESPALDOS_DIR = BASE_DIR / "respaldos"
RESPALDOS_A_CONSERVAR = 52  # ~1 año de respaldos semanales, para no hacer crecer el repo sin límite

# Planilla de Google publicada (Archivo → Compartir → Publicar en la Web → CSV),
# cuenta monitor.de.precios1@gmail.com (ver TRASPASO.md), no una cuenta personal.
#
# Pestaña "Arreglar Link" (columnas sku_interno,url_nuevo,nota, para reemplazar
# un URL que murió sin tocar código): todavía no existe en la planilla actual,
# función desactivada hasta que se cree esa pestaña y se publique (ver
# TRASPASO.md). El resto sigue funcionando igual sin ella.
OVERRIDES_CSV_URL = ""
# Pestaña "Catálogo" (única pestaña de la planilla hoy): el catálogo COMPLETO,
# mismas columnas que productos.csv — cada fila es un producto. No hay columna
# 'Acción': agregar una fila = alta, borrar una fila = baja, cambiar un valor =
# edición. Cada corrida reemplaza productos.csv por el contenido validado de
# esta pestaña y el propio workflow lo commitea (ver sincronizar_desde_catalogo).
CATALOGO_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTMZ7qyGdu79TJ5CUPN5dfIf4YZDgV9JqDpDdW8dA_jiqCrYDcW3RO_hGqjRp12QnKWKTvlkKvV1nWX/pub?gid=0&single=true&output=csv"

HEADERS_GENERICOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json,application/xml;q=0.9,*/*;q=0.8",
}

COLUMNAS_PRODUCTOS = [
    "sku_interno", "producto", "marca", "metros_totales", "retailer", "url",
    "categoria", "subcategoria", "rollos", "metros_rollo", "unidades",
]

# Encabezados en español que ve el equipo en la planilla de Google -> nombre
# interno que usa el código. Permite que la planilla sea legible sin tocar el
# resto del programa; una columna que llegue con el nombre interno (viejo)
# también funciona, .rename() solo toca las que coinciden con este mapa.
ENCABEZADOS_PRODUCTOS = {
    "Código": "sku_interno", "Producto": "producto",
    "Marca": "marca", "Retailer": "retailer", "Link": "url",
    "Categoría": "categoria", "Subcategoría": "subcategoria",
    "Rollos": "rollos", "Metros por rollo": "metros_rollo",
    "Metros totales": "metros_totales", "Unidades": "unidades",
    "Grupo": "grupo_id", "Nombre estándar": "nombre_estandar",
}
ENCABEZADOS_URL_FIXES = {"Código": "sku_interno", "Link nuevo": "url_nuevo", "Nota": "nota"}

# grupo_id/nombre_estandar son opcionales (no todo producto está cruzado con
# otro retailer).
COLUMNAS_GRUPO = ["grupo_id", "nombre_estandar"]
COLUMNAS_NUMERICAS = ["metros_totales", "rollos", "metros_rollo", "unidades"]
COLUMNAS_TEXTO_OBLIGATORIAS = ["producto", "marca", "categoria", "subcategoria"]

# Bajo qué proporción del catálogo actual se considera que la planilla vino
# "rota" (borrado masivo por error, columnas movidas, pegado a medias, etc.)
# y se rechaza el cambio COMPLETO en vez de aplicarlo a medias — ver
# sincronizar_desde_catalogo.
UMBRAL_RECHAZO_CATALOGO = 0.7


def cargar_catalogo_planilla():
    """Devuelve (DataFrame_o_None, aviso_o_None) con el catálogo COMPLETO que el
    equipo mantiene en la pestaña 'Catálogo' de la planilla de Google: cada fila
    es un producto, con las mismas columnas que productos.csv. No hay columna
    'Acción' — agregar una fila es un alta, borrarla es una baja, cambiar un
    valor es una edición. Si la planilla no responde, cae a la última copia
    buena (catalogo_cache.csv) para no frenar la corrida ni perder el catálogo."""
    if not CATALOGO_CSV_URL:
        return None, None
    try:
        res = requests.get(CATALOGO_CSV_URL, headers=HEADERS_GENERICOS, timeout=15)
        res.raise_for_status()
        df = pd.read_csv(io.StringIO(res.text)).rename(columns=ENCABEZADOS_PRODUCTOS)
        if "sku_interno" not in df.columns:
            return None, "la planilla no tiene columna 'Código'; se ignoró el cambio"
        for col in COLUMNAS_PRODUCTOS + COLUMNAS_GRUPO:
            if col not in df.columns:
                df[col] = pd.NA
        df = df[COLUMNAS_PRODUCTOS + COLUMNAS_GRUPO]
        df.to_csv(CATALOGO_CACHE_PATH, index=False)
        return df, None
    except Exception as e:
        if CATALOGO_CACHE_PATH.exists():
            try:
                return pd.read_csv(CATALOGO_CACHE_PATH), f"planilla no disponible ({str(e)[:60]}); usando copia local"
            except Exception:
                pass
        return None, f"planilla de catálogo no disponible ({str(e)[:60]})"


def _parsear_numero(val):
    """Convierte un valor de una columna numérica de la planilla a número.
    Google Sheets, con configuración regional en español, publica el CSV con
    coma como separador decimal (ej. '21,3' en vez de '21.3') aunque el valor
    se haya tipeado con punto — sin este fallback, cualquier medida con
    decimales (21.3 m, 37.5 m, etc.) se rechazaría como 'no numérica' siendo
    perfectamente válida. Devuelve (es_válido, valor_normalizado)."""
    if pd.isna(val) or str(val).strip() == "":
        return True, val
    texto = str(val).strip()
    numero = pd.to_numeric(pd.Series([texto]), errors="coerce").iloc[0]
    if pd.isna(numero) and "," in texto and "." not in texto:
        numero = pd.to_numeric(pd.Series([texto.replace(",", ".")]), errors="coerce").iloc[0]
    return pd.notna(numero), numero


def validar_catalogo(df, retailers_validos):
    """Revisa fila por fila el catálogo bajado de la planilla. Una fila con un
    problema (código repetido o vacío, retailer que no existe, link sin http,
    falta texto obligatorio, un campo numérico con letras) queda AFUERA del
    catálogo que se va a aplicar y se reporta en 'problemas' — así una fila mal
    cargada no frena a las demás, pero tampoco se aplica a medias en silencio.
    Devuelve (df_valido, problemas: list[str])."""
    problemas = []
    df = df.copy()
    df["sku_interno"] = df["sku_interno"].astype(str).str.strip()
    # object, no el dtype numérico/"str" estricto que a veces infiere pandas:
    # estas columnas reciben más abajo un número normalizado por celda
    # (_parsear_numero), y un dtype estricto no deja mezclar eso con vacíos.
    for col in COLUMNAS_NUMERICAS:
        if col in df.columns:
            df[col] = df[col].astype(object)
    vistos = set()
    filas_ok = []
    for idx, fila in df.iterrows():
        # str(...) explícito acá (no alcanza con el .astype(str) de arriba):
        # iterrows() arma cada fila mezclando los tipos de TODAS sus columnas,
        # y una fila casi vacía (código en blanco al final de la planilla,
        # con el resto de las columnas numéricas también vacías) puede volver
        # a convertir el texto 'nan' en un float NaN real para esa fila puntual.
        sku = str(fila["sku_interno"]).strip()
        if not sku or sku.lower() == "nan":
            problemas.append("(fila sin Código): se ignoró, falta el Código")
            continue
        if sku in vistos:
            problemas.append(f"{sku}: Código repetido en la planilla, se usó la primera fila y se ignoraron las siguientes")
            continue
        errs = []
        if str(fila.get("retailer", "")).strip() not in retailers_validos:
            errs.append(f"Retailer '{fila.get('retailer')}' no es una clave válida")
        if not str(fila.get("url", "")).strip().startswith("http"):
            errs.append("falta el Link o no empieza con http")
        for col, nombre in [("producto", "Producto"), ("marca", "Marca"), ("categoria", "Categoría"), ("subcategoria", "Subcategoría")]:
            val = fila.get(col)
            if pd.isna(val) or not str(val).strip():
                errs.append(f"falta {nombre}")
        numeros_normalizados = {}
        for col in COLUMNAS_NUMERICAS:
            val = fila.get(col)
            ok, numero = _parsear_numero(val)
            if not ok:
                errs.append(f"'{col}' = '{val}' no es un número válido")
            else:
                numeros_normalizados[col] = numero
        if errs:
            problemas.append(f"{sku}: " + "; ".join(errs))
            continue
        for col, numero in numeros_normalizados.items():
            df.loc[idx, col] = numero
        vistos.add(sku)
        filas_ok.append(idx)

    df_valido = df.loc[filas_ok].reset_index(drop=True)

    # Aviso informativo (no excluye filas): un mismo grupo_id con nombre_estandar
    # distinto entre sus filas normalmente es un error de tipeo en una de ellas.
    if "grupo_id" in df_valido.columns and "nombre_estandar" in df_valido.columns:
        con_grupo = df_valido.dropna(subset=["grupo_id"])
        for gid, sub in con_grupo.groupby(con_grupo["grupo_id"].map(_normalizar_grupo_id)):
            nombres = set(sub["nombre_estandar"].dropna().astype(str).str.strip()) - {""}
            if len(nombres) > 1:
                problemas.append(f"⚠️ Grupo {gid}: nombre estándar distinto entre sus filas ({', '.join(sorted(nombres))}), revisar cuál es el correcto")

    return df_valido, problemas


def _normalizar_grupo_id(val):
    """'9', 9, 9.0 tienen que compararse como el mismo grupo — pandas guarda
    grupo_id como float (9.0) al leer el CSV, pero alguien tipeando en la
    planilla escribe '9'. Sin esto, comparar como texto crudo los trata como
    grupos distintos y el cruce entre retailers queda roto en silencio."""
    texto = str(val).strip()
    try:
        return str(int(float(texto)))
    except ValueError:
        return texto


def sincronizar_desde_catalogo(df_nuevo, retailers_cfg):
    """Reemplaza productos.csv por el catálogo validado de la planilla,
    preservando el bloque de comentarios final. Es "todo o nada" a nivel
    catálogo: si no queda ninguna fila válida, o si el catálogo válido tiene
    más de un 30% menos de productos que el actual (señal de un borrado
    masivo por error, columnas movidas, o la planilla vacía/rota), NO se
    aplica ningún cambio y sigue la última copia buena — eso sí se avisa
    fuerte. Los problemas puntuales de fila (ver validar_catalogo) sí se
    reportan pero no frenan al resto. Devuelve un resumen para el panel de
    salud del dashboard."""
    resumen = {"aplicado": False, "filas_aplicadas": 0, "problemas": [], "motivo_rechazo": None}
    if df_nuevo is None or df_nuevo.empty:
        return resumen

    texto = PRODUCTOS_PATH.read_text(encoding="utf-8")
    lineas = texto.splitlines()
    ultimo_dato = max(i for i, l in enumerate(lineas) if l.strip() and not l.lstrip().startswith("#"))
    pie = lineas[ultimo_dato + 1:]  # línea en blanco + comentarios finales, se preservan tal cual

    productos_actuales = pd.read_csv(PRODUCTOS_PATH, comment="#", skip_blank_lines=True).dropna(subset=["sku_interno"])

    df_valido, problemas = validar_catalogo(df_nuevo, set(retailers_cfg.keys()))
    resumen["problemas"] = problemas

    if df_valido.empty:
        resumen["motivo_rechazo"] = "la planilla no tiene ninguna fila válida; se ignoró todo el cambio y sigue el catálogo anterior"
        return resumen

    minimo_esperado = len(productos_actuales) * UMBRAL_RECHAZO_CATALOGO
    if len(productos_actuales) > 0 and len(df_valido) < minimo_esperado:
        resumen["motivo_rechazo"] = (
            f"la planilla trae {len(df_valido)} producto(s) válido(s) contra {len(productos_actuales)} "
            "que hay ahora (más de 30% menos) — parece un error grave (borrado masivo, columnas movidas, "
            "planilla pegada a medias), se ignoró todo el cambio para no perder el catálogo"
        )
        return resumen

    columnas_csv = list(productos_actuales.columns) if not productos_actuales.empty else COLUMNAS_PRODUCTOS + COLUMNAS_GRUPO
    df_valido = df_valido.reindex(columns=columnas_csv)

    buf = io.StringIO()
    df_valido.to_csv(buf, index=False)
    nuevo_texto = buf.getvalue().rstrip("\n") + "\n" + ("\n".join(pie) + "\n" if pie else "")
    PRODUCTOS_PATH.write_text(nuevo_texto, encoding="utf-8")
    resumen["aplicado"] = True
    resumen["filas_aplicadas"] = len(df_valido)
    return resumen


def cargar_config():
    """Devuelve (productos_df, retailers_cfg, meta). meta trae el resultado de
    sincronizar el catálogo desde la planilla (aplicado, filas, problemas de
    fila, motivo de rechazo si lo hubo) y cualquier aviso, para el panel de
    salud."""
    with open(RETAILERS_PATH, "r", encoding="utf-8") as f:
        retailers = yaml.safe_load(f)
    catalogo, aviso_catalogo = cargar_catalogo_planilla()
    resumen = {"aplicado": False, "filas_aplicadas": 0, "problemas": [], "motivo_rechazo": None}
    if catalogo is not None and not catalogo.empty:
        resumen = sincronizar_desde_catalogo(catalogo, retailers)
        if resumen["aplicado"]:
            print(f"📋 Catálogo aplicado desde la planilla: {resumen['filas_aplicadas']} producto(s)")
        if resumen["motivo_rechazo"]:
            print(f"🚫 Catálogo de la planilla RECHAZADO: {resumen['motivo_rechazo']}")
        if resumen["problemas"]:
            print(f"⚠️ Filas del catálogo con problemas (ignoradas o solo avisadas): {resumen['problemas']}")
    if aviso_catalogo:
        print(f"⚠️ Catálogo: {aviso_catalogo}")
    productos = pd.read_csv(PRODUCTOS_PATH, comment="#", skip_blank_lines=True).dropna(subset=["sku_interno"])
    return productos, retailers, {"sku_planilla": resumen, "aviso": aviso_catalogo}


def guardar_respaldo_semanal():
    """Guarda una copia fechada de productos.csv en respaldos/ (a lo sumo una
    por semana calendario) para poder volver a un estado de hace varias
    semanas sin necesitar git ni GitHub — con abrir la carpeta 'respaldos' en
    GitHub alcanza. Es un respaldo del catálogo en sí; el respaldo de la
    planilla de Google (por si alguien se equivoca ahí) es el historial de
    versiones propio de Sheets, ver TRASPASO.md. Borra los respaldos más
    viejos que RESPALDOS_A_CONSERVAR para no hacer crecer el repo sin límite."""
    RESPALDOS_DIR.mkdir(exist_ok=True)
    ahora = datetime.now(ZoneInfo("America/Santiago")).isocalendar()
    destino = RESPALDOS_DIR / f"productos_{ahora.year}-S{ahora.week:02d}.csv"
    if not destino.exists():
        shutil.copy(PRODUCTOS_PATH, destino)
    for viejo in sorted(RESPALDOS_DIR.glob("productos_*.csv"))[:-RESPALDOS_A_CONSERVAR]:
        viejo.unlink()


def cargar_overrides_url():
    """Devuelve ({sku_interno: url_nuevo}, aviso_o_None) con las correcciones de
    URL que el equipo cargó en la planilla de Google. Si la planilla no responde
    (sin internet, la despublicaron), cae a la última copia buena guardada en el
    repo (url_overrides_cache.json) para no perder correcciones ya hechas ni
    voltear la corrida. Cada lectura exitosa refresca esa copia."""
    if not OVERRIDES_CSV_URL:
        return {}, None
    try:
        res = requests.get(OVERRIDES_CSV_URL, headers=HEADERS_GENERICOS, timeout=15)
        res.raise_for_status()
        df = pd.read_csv(io.StringIO(res.text)).rename(columns=ENCABEZADOS_URL_FIXES)
        df = df.dropna(subset=["sku_interno", "url_nuevo"])
        overrides = {}
        for _, r in df.iterrows():
            sku = str(r["sku_interno"]).strip()
            url = str(r["url_nuevo"]).strip()
            if sku and url.startswith("http"):
                overrides[sku] = url
        with open(OVERRIDES_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(overrides, f, ensure_ascii=False, indent=2)
        return overrides, None
    except Exception as e:
        if OVERRIDES_CACHE_PATH.exists():
            try:
                with open(OVERRIDES_CACHE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f), f"planilla no disponible ({str(e)[:60]}); usando copia local"
            except Exception:
                pass
        return {}, f"planilla no disponible y sin copia local ({str(e)[:60]})"

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


def _consultar_liquimax(url: str):
    """liquimax.cl corre sobre la plataforma Bolder, que expone la ficha como
    JSON limpio agregando '.json' a la URL del producto (sin protección
    anti-bot). Trae price / regular_price / sale_price y 'available'. Además
    es mayorista: 'volume_discount.tiers' trae el precio rebajado comprando
    2+ unidades (5º valor devuelto, análogo al precio socio-2-un de Alvi)."""
    api = url.split("?")[0].rstrip("/") + ".json"
    try:
        res = requests.get(api, headers=HEADERS_GENERICOS, timeout=12)
        if res.status_code != 200:
            return None, None, False, f"HTTP {res.status_code}", None
        d = res.json()
        p = d.get("product", d)
        precio = p.get("sale_price") or p.get("price")
        if not precio:
            return None, None, False, "Sin precio en la respuesta", None
        precio_normal = p.get("regular_price") or precio
        disponible = bool(p.get("available")) and not p.get("blocked")
        precio_2un = None
        vd = p.get("volume_discount") or {}
        tiers = [t for t in vd.get("tiers", []) if t.get("result")]
        if tiers:
            precio_2un = float(min(t["result"] for t in tiers))
        return float(precio), float(precio_normal), disponible, None, precio_2un
    except Exception as e:
        return None, None, False, f"Error Liquimax: {str(e)[:80]}", None


def _consultar_imanweb(url: str):
    """imanweb.cl es WooCommerce con la Store API abierta
    (/wp-json/wc/store/v1/products?slug=...). El precio de lista/oferta a veces
    lo pone un plugin de descuento que la Store API no refleja en 'prices'
    (queda todo igual y on_sale=False), pero sí aparece en 'price_html' como
    <del>lista</del> <ins>oferta</ins> — de ahí se saca el par. Es mayorista:
    el precio es el de la 'manga' (varios paquetes); el nº de paquetes se
    carga en la columna 'unidades' de productos.csv."""
    slug = url.split("?")[0].rstrip("/").split("/")[-1]
    try:
        res = requests.get(
            "https://www.imanweb.cl/wp-json/wc/store/v1/products",
            params={"slug": slug}, headers=HEADERS_GENERICOS, timeout=12,
        )
        if res.status_code != 200:
            return None, None, False, f"HTTP {res.status_code}"
        data = res.json()
        if not data:
            return None, None, False, f"slug '{slug}' no encontrado"
        p = data[0]
        pr = p.get("prices") or {}
        minor = int(pr.get("currency_minor_unit") or 0)
        div = 10 ** minor
        precio = float(pr.get("price")) / div if pr.get("price") else None
        if not precio:
            return None, None, False, "Sin precio en la respuesta"
        precio_normal = precio
        m = re.search(r"<del[^>]*>.*?([\d.]+)</span>", p.get("price_html") or "", re.S)
        if m:
            try:
                precio_normal = float(m.group(1).replace(".", ""))
            except ValueError:
                pass
        disponible = bool(p.get("is_in_stock")) and bool(p.get("is_purchasable"))
        return precio, max(precio_normal, precio), disponible, None
    except Exception as e:
        return None, None, False, f"Error imanweb: {str(e)[:80]}"


def _consultar_dimak(url: str):
    """dimakonline.cl es una tienda VTEX estándar — la API pública de catálogo
    devuelve el precio sin ninguna protección. Se busca por el 'linkText'
    (el tramo de la URL antes de '/p'). Es mayorista: el precio es el de la
    'manga'/display; los paquetes por manga van en 'unidades' en
    productos.csv (el dato está en la spec 'detalleProductos' de la ficha)."""
    link_text = url.split("?")[0].rstrip("/").split("/")[-2] if url.rstrip("/").endswith("/p") else url.rstrip("/").split("/")[-1]
    try:
        res = requests.get(
            f"https://www.dimakonline.cl/api/catalog_system/pub/products/search/{link_text}/p",
            headers=HEADERS_GENERICOS, timeout=12,
        )
        if res.status_code not in (200, 206):
            return None, None, False, f"HTTP {res.status_code}"
        data = res.json()
        if not data:
            return None, None, False, f"linkText '{link_text}' no encontrado"
        oferta = data[0]["items"][0]["sellers"][0]["commertialOffer"]
        precio = oferta.get("Price")
        if not precio:
            return None, None, False, "Sin precio en la respuesta"
        precio_normal = oferta.get("ListPrice") or oferta.get("PriceWithoutDiscount") or precio
        disponible = bool(oferta.get("IsAvailable")) and (oferta.get("AvailableQuantity") or 0) > 0
        return float(precio), float(precio_normal), disponible, None
    except Exception as e:
        return None, None, False, f"Error dimak: {str(e)[:80]}"


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


def _consultar_alvi(url: str):
    """Alvi es mayorista: además del precio de lista, muestra un precio
    'socio' que baja según cuántas unidades compras (1 unidad, 2+ unidades).
    Los tres viven ya calculados en el JSON de hidratación de Next.js
    (__NEXT_DATA__), sin necesidad de regex frágiles sobre el texto
    renderizado — se parsea como el JSON válido que es. Verificado
    27/08/2026 contra la ficha real: sellers[0].listPrice = precio lista,
    priceSteps = [{minQuantity:1, promotionalPrice}, {minQuantity:2, ...}]."""
    for intento in range(3):
        impersonate_profile = ["chrome124", "safari15_5", "chrome120"][intento]
        try:
            res = cffi_requests.get(url, headers=HEADERS_GENERICOS, impersonate=impersonate_profile, timeout=12)
            if res.status_code == 200:
                m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', res.text, re.DOTALL)
                producto = None
                if m:
                    try:
                        data = json.loads(m.group(1))
                        producto = data.get("props", {}).get("pageProps", {}).get("product")
                    except json.JSONDecodeError:
                        producto = None
                if not producto:
                    if intento == 2: return None, None, None, False, "No se encontró el producto en Alvi"
                else:
                    seller = (producto.get("sellers") or [{}])[0]
                    precio_lista = seller.get("listPrice")
                    pasos = producto.get("priceSteps") or []
                    precio_socio1 = next((p.get("promotionalPrice") for p in pasos if p.get("minQuantity") == 1), None)
                    precio_socio2 = next((p.get("promotionalPrice") for p in pasos if p.get("minQuantity") == 2), None)
                    disponible = (seller.get("availableQuantity") or 0) > 0
                    if precio_lista:
                        return (
                            float(precio_lista),
                            float(precio_socio1) if precio_socio1 else None,
                            float(precio_socio2) if precio_socio2 else None,
                            disponible, None,
                        )
                    if intento == 2: return None, None, None, False, "Sin precio en Alvi"
            else:
                if intento == 2: return None, None, None, False, f"HTTP {res.status_code}"
        except Exception as e:
            if intento == 2: return None, None, None, False, f"Error Alvi: {str(e)[:60]}"
            time.sleep(1)
            continue
    return None, None, None, False, "Falla desconocida"


def procesar_lote(retailer_key, lista_productos, cfg):
    salida = []
    for prod in lista_productos:
        extra = None
        metodo = cfg.get("metodo")
        if retailer_key == "alvi":
            precio_lista, precio_socio1, precio_socio2, disp, err = _consultar_alvi(prod["url"])
            # Se compara/agrupa con el resto usando el precio socio de 1
            # unidad (el lograble sin mínimo de compra) como "precio", y el
            # de lista como "precio normal" — el de 2+ unidades es propio de
            # Alvi y se guarda aparte para mostrarlo como tercera columna.
            p = precio_socio1 or precio_lista
            pn = precio_lista
            if p:
                extra = {"precio_socio2": precio_socio2}
        elif metodo == "lider_api":
            p, pn, disp, err = _consultar_lider_api(prod["url"])
        elif metodo == "instaleap":
            p, pn, disp, err = _consultar_instaleap(prod["url"], cfg)
        elif metodo == "liquimax":
            p, pn, disp, err, p2 = _consultar_liquimax(prod["url"])
            if p and p2:
                extra = {"precio_socio2": p2}
        elif metodo == "imanweb":
            p, pn, disp, err = _consultar_imanweb(prod["url"])
        elif metodo == "dimak":
            p, pn, disp, err = _consultar_dimak(prod["url"])
        else:
            p, pn, disp, err = _consultar_curl_cffi(prod["url"], cfg)

        if p:
            if pn and p < pn:
                print(f"✅ {retailer_key} | {prod['sku_interno']} -> OFERTA: ${p} (Normal: ${pn})")
            else:
                print(f"✅ {retailer_key} | {prod['sku_interno']} -> Extraído: ${p}")
        else:
            print(f"❌ {retailer_key} | {prod['sku_interno']} -> FALLÓ: {err}")

        salida.append((retailer_key, prod["sku_interno"], p, pn, disp, err, extra))
        # Espacio entre productos del MISMO retailer — sin esto, varios
        # productos seguidos del mismo sitio uno detrás de otro es justo el
        # patrón que hace escalar bloqueos tipo Cloudflare (ej. Tottus).
        time.sleep(random.uniform(0.8, 1.8))
    return salida

LIMITE_HISTORIAL_SEMANAS = 104  # ~2 años — alcanza para comparar temporadas de un año a otro sin acumular para siempre.


def actualizar_historial(resultados_actuales, fecha_hoy):
    """Guarda un precio por SKU por semana (no cada corrida) para poder ver
    más adelante cómo evoluciona el precio en el tiempo, sin que el archivo
    crezca sin control corriendo 3 veces al día. Si ya hay un dato de esta
    misma semana ISO para ese SKU, se reemplaza por el más reciente; solo se
    guardan precios recién obtenidos (resultados_actuales), nunca el
    fallback de "último precio conocido" de un SKU que falló esta corrida —
    eso repetiría un dato viejo como si fuera nuevo. Los datos de más de
    LIMITE_HISTORIAL_SEMANAS se podan en cada corrida."""
    if not resultados_actuales:
        return
    ahora = datetime.now(ZoneInfo("America/Santiago"))
    semana = ahora.strftime("%G-W%V")

    filas = {}
    if HISTORIAL_PATH.exists():
        hist_df = pd.read_csv(HISTORIAL_PATH)
        for _, r in hist_df.iterrows():
            filas[(r["sku_interno"], r["semana"])] = r.to_dict()

    for sku, res in resultados_actuales.items():
        filas[(sku, semana)] = {
            "semana": semana,
            "sku_interno": sku,
            "precio": res["precio"],
            "precio_normal": res["precio_normal"],
            "fecha_act": fecha_hoy,
        }

    # Las semanas ISO ("YYYY-Www", ancho fijo) ordenan igual como texto que
    # como fecha, así que comparar strings alcanza para podar lo viejo.
    corte = (ahora - timedelta(weeks=LIMITE_HISTORIAL_SEMANAS)).strftime("%G-W%V")
    filas = {k: v for k, v in filas.items() if k[1] >= corte}

    df_out = pd.DataFrame(filas.values()).sort_values(["sku_interno", "semana"])
    df_out.to_csv(HISTORIAL_PATH, index=False)


if __name__ == "__main__":
    print("🤖 Iniciando motor de extracción de precios (Modo Autónomo)...")
    
    productos, retailers_cfg, meta_config = cargar_config()
    guardar_respaldo_semanal()

    overrides_url, overrides_aviso = cargar_overrides_url()
    if overrides_url:
        aplicados = productos["sku_interno"].isin(list(overrides_url)).sum()
        productos["url"] = productos.apply(
            lambda row: overrides_url.get(row["sku_interno"], row["url"]), axis=1
        )
        print(f"🔧 {aplicados} URL(s) reemplazado(s) desde la planilla de correcciones")
    else:
        aplicados = 0
    if overrides_aviso:
        print(f"⚠️ Overrides: {overrides_aviso}")

    # Retailers marcados "deshabilitado" en retailers.yaml no se intentan
    # scrapear (ahorra tiempo y ruido cuando el bloqueo es total y no vale la
    # pena reintentar cada corrida). Sus SKU siguen en productos.csv y en el
    # dashboard, mostrando el último precio conocido con el aviso de siempre —
    # el mismo camino que ya existe para un SKU que falla, nada nuevo que romper.
    retailers_desactivados = {k for k, v in retailers_cfg.items() if v.get("deshabilitado")}
    if retailers_desactivados:
        n_desactivados = productos["retailer"].isin(retailers_desactivados).sum()
        print(f"⏸️ {n_desactivados} SKU(s) saltados, retailer(s) deshabilitado(s): {', '.join(sorted(retailers_desactivados))}")

    grupos = {}
    for _, prod in productos.iterrows():
        if prod["retailer"] in retailers_desactivados:
            continue
        grupos.setdefault(prod["retailer"], []).append(prod)

    resultados_actuales = {}
    fallos_ultima_corrida = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futuros = [executor.submit(procesar_lote, k, v, retailers_cfg.get(k, {})) for k, v in grupos.items()]
        for futuro in concurrent.futures.as_completed(futuros):
            for retailer_key, sku, p, pn, disp, err, extra in futuro.result():
                if p and p > 0:
                    entry = {
                        "sku_interno": sku, "precio": p, "precio_normal": pn,
                        "estado": "Disponible", "error": None
                    }
                    if extra:
                        entry.update({k: v for k, v in extra.items() if v is not None})
                    resultados_actuales[sku] = entry
                else:
                    fallos_ultima_corrida.append({"retailer": retailer_key, "sku_interno": sku, "error": err})

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

    actualizar_historial(resultados_actuales, fecha_hoy)

    # Snapshot de la última corrida (no historial acumulado, se pisa cada
    # vez) para que la página pueda mostrar un indicador de salud sin que
    # alguien tenga que ir a leer los logs de GitHub Actions.
    with open(ESTADO_SCRAPER_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "fecha": fecha_hoy,
            "total_skus": len(productos),
            "exitosos": len(resultados_actuales),
            "fallos": fallos_ultima_corrida,
            "overrides_url_aplicados": int(aplicados),
            "overrides_url_aviso": overrides_aviso,
            "catalogo_planilla": meta_config["sku_planilla"],
            "catalogo_planilla_aviso": meta_config["aviso"],
            "retailers_desactivados": sorted(retailers_desactivados),
            "sku_desactivados": int(productos["retailer"].isin(retailers_desactivados).sum()),
        }, f, ensure_ascii=False, indent=2)

    print("🏁 Extracción terminada. JSON actualizado.")
