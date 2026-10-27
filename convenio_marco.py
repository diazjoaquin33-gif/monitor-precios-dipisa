"""Monitor de proveedores de Convenio Marco (Mercado Público): papel tissue y resmas.

Baja el listado de productos de la categoría y, de la ficha de cada uno, los
proveedores que despachan a cada una de las 18 regiones, con su precio neto y
días de entrega (todo viene en la misma ficha: no hay que pedir páginas extra). Compara con la corrida anterior y deja un resumen de qué cambió
(proveedores nuevos, retirados, cambios de precio, productos nuevos/retirados).

No usa API ni login: los datos vienen dentro del HTML público de la ficha
(bloque JSON "Webkul_MpAssignProduct/js/assignproduct" + filas de la tabla de
proveedores). Si Convenio Marco cambia ese bloque, el script falla fuerte y
avisa en estado_convenio_marco.json en vez de dejar datos a medias.

Catálogos (ver CATALOGOS): cada uno tiene sus propios archivos de datos. Para
agregar otro basta una entrada nueva en CATALOGOS (y una página en pages/).

Uso:
    python convenio_marco.py                      # todos los catálogos
    python convenio_marco.py --catalogo resmas    # solo uno
    python convenio_marco.py --prueba 3           # solo los primeros 3 productos de cada uno
"""
import argparse
from html import unescape
import threading
from concurrent.futures import ThreadPoolExecutor
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

BASE = "https://conveniomarco.mercadopublico.cl"
LISTADO = BASE + "/aseo3/productos-y-articulos-de-aseo-e-higiene-personal/papel-tissue"
# Cada catálogo es una lista de productos en Convenio Marco: una categoría o una
# búsqueda. "archivo" define los nombres de sus datos (<archivo>_proveedores.csv,
# <archivo>_cambios.csv y estado_<archivo>.json).
CATALOGOS = {
    "tissue": {"nombre": "Papel tissue", "archivo": "convenio_marco",
               "url": LISTADO, "params": {}},
    "resmas": {"nombre": "Resmas (papel fotocopia)", "archivo": "convenio_marco_resmas",
               "url": BASE + "/escritorio3/catalogsearch/result/index/", "params": {"q": "resmas"}},
}
# id interno del sitio -> nombre, de norte a sur (las 2 últimas son territorios insulares)
REGIONES = {
    "15": "Arica y Parinacota", "1": "Tarapacá", "2": "Antofagasta", "3": "Atacama",
    "4": "Coquimbo", "5": "Valparaíso", "13": "Metropolitana de Santiago",
    "6": "O'Higgins", "7": "Maule", "16": "Ñuble", "8": "Biobío", "9": "La Araucanía",
    "14": "Los Ríos", "10": "Los Lagos", "11": "Aysén", "12": "Magallanes",
    "17": "Juan Fernández", "18": "Isla de Pascua",
}
REGION_REF = "13"  # región de referencia (Metropolitana): la que usa el panel por defecto
PAUSA_SEG = 1.0   # cortesía con el sitio: pausa de cada hilo entre fichas
HILOS = 4         # ~4 fichas en paralelo: ~700 fichas en ~4 min en vez de ~12
REINTENTOS = 2
TIMEOUT_SEG = 25
PRESUPUESTO_MIN = 20   # por catálogo: si pasa de esto se aborta limpia (el workflow corta a los 45 en total)
FALLAS_SEGUIDAS_MAX = 15  # sitio caído/bloqueado: abortar rápido en vez de agotar el tiempo

DIR = Path(__file__).parent


def rutas(catalogo):
    """(proveedores.csv, cambios.csv, estado.json) del catálogo."""
    base = CATALOGOS[catalogo]["archivo"]
    return DIR / f"{base}_proveedores.csv", DIR / f"{base}_cambios.csv", DIR / f"estado_{base}.json"

try:  # GitHub Actions corre en UTC: la fecha se guarda en hora de Chile
    from zoneinfo import ZoneInfo
    TZ_CHILE = ZoneInfo("America/Santiago")
except Exception:  # Windows sin tzdata: se usa la hora local del equipo (Chile)
    TZ_CHILE = None

HEADERS = {"User-Agent": "Mozilla/5.0 (monitor-precios-dipisa)"}
# Se guarda UNA fila por producto y proveedor, con una columna de precio (p_<id>)
# y una de días de entrega (d_<id>) por región. Una fila por (producto, proveedor,
# región) serían ~10 veces más filas y el repo crecería muy rápido cada semana.
COLS_BASE = ["producto_id", "producto", "proveedor_id", "proveedor", "rut"]
COLS_PRECIO = [f"p_{r}" for r in REGIONES]
COLS_DIAS = [f"d_{r}" for r in REGIONES]
COLUMNAS = COLS_BASE + COLS_PRECIO + COLS_DIAS
COLS_CAMBIOS = ["fecha", "tipo", "producto_id", "producto", "proveedor", "rut", "regiones", "detalle"]
DTYPES_CSV = {"producto_id": str, "proveedor_id": str, "rut": str}


def log(msg):
    print(msg, flush=True)


def _get(url, params=None):
    ultimo = None
    for intento in range(REINTENTOS):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT_SEG)
            if r.status_code == 200:
                return r.content.decode("utf-8", errors="replace")
            ultimo = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            ultimo = str(e)
        log(f"  reintento {intento + 1}/{REINTENTOS} de {url[-60:]}: {ultimo}")
        time.sleep(2 * (intento + 1))
    raise RuntimeError(f"No se pudo leer {url}: {ultimo}")


PRODUCTO_RE = re.compile(
    r'<a[^>]+class="[^"]*product-item-link[^"]*"[^>]+href="([^"]+)"[^>]*>\s*([^<]+?)\s*</a>')


def listar_productos(catalogo, limite=None):
    """Recorre el listado del catálogo (58 por página) y devuelve [(id, nombre, url)].
    Se detiene cuando una página ya no trae productos nuevos, así que se adapta
    solo a cuántos haya. Avisa si el total que declara el sitio no coincide."""
    cfg = CATALOGOS[catalogo]
    productos, vistos, pagina, total = [], set(), 1, None
    while True:
        html = _get(cfg["url"], {**cfg["params"], "p": pagina, "product_list_limit": 58})
        if total is None:
            m = re.search(r'id="toolbar-amount">(.*?)</p>', html, re.S)
            nums = re.findall(r"\d+", re.sub(r"<[^>]+>", " ", m.group(1))) if m else []
            total = int(nums[-1]) if nums else None
        nuevos = 0
        for url, nombre in PRODUCTO_RE.findall(html):
            mid = re.search(r"-(\d+)$", url.rstrip("/"))
            if not mid or mid.group(1) in vistos:
                continue
            vistos.add(mid.group(1))
            nuevos += 1
            productos.append((mid.group(1), re.sub(r"\s+", " ", unescape(nombre)).strip(), url))
        if nuevos == 0:
            break
        if limite and len(productos) >= limite:
            return productos[:limite]
        pagina += 1
        time.sleep(PAUSA_SEG)
    if total and len(productos) != total:
        log(f"AVISO: el sitio declara {total} productos y se leyeron {len(productos)}")
    return productos


def _config_json(html):
    for m in re.finditer(r'<script type="text/x-magento-init">(.*?)</script>', html, re.S):
        if "Webkul_MpAssignProduct/js/assignproduct" in m.group(1):
            return json.loads(m.group(1))["body"]["Webkul_MpAssignProduct/js/assignproduct"]
    raise RuntimeError("no se encontró el bloque de proveedores (¿cambió el sitio?)")


def _hijos_region(html):
    """{id_region: id del producto-hijo}. La ficha es un producto configurable:
    cada región es una variante."""
    hijos = {rid: hijo for rid, hijo in
             re.findall(r'"id":"(\d+)","label":"[^"]*","products":\["(\d+)"\]', html)
             if rid in REGIONES}
    if REGION_REF not in hijos:
        raise RuntimeError("no se encontraron las variantes por región (¿cambió el sitio?)")
    return hijos


def _proveedores_html(html):
    """{proveedor_id: (nombre, rut)} desde las filas de la tabla. Algunos convenios
    no publican el RUT en la ficha: en ese caso queda vacío."""
    datos = {}
    for bloque in re.split(r'(?=<tr[^>]*flag-row-seller)', html)[1:]:
        fin = bloque.find("</tr>")
        bloque = bloque[:fin] if fin > 0 else bloque
        sid = re.search(r'data-sellerid="(\d+)"', bloque)
        nom = re.search(r'data-name="([^"]*)"', bloque)
        rut = re.search(r'data-rut="([^"]*)"', bloque)
        if sid and nom:
            datos.setdefault(sid.group(1), (unescape(nom.group(1)).strip(),
                                            rut.group(1).strip() if rut else ""))
    return datos


def _oferta_a_proveedor(html):
    """{id_oferta: proveedor_id} desde las filas de la tabla (los atributos
    pueden venir en cualquier orden, así que se leen por separado)."""
    mapa = {}
    for tag in re.findall(r'<tr[^>]*flag-row-seller[^>]*>', html, re.S):
        o = re.search(r'data-id="(\d+)"', tag)
        sid = re.search(r'data-sellerid="(\d+)"', tag)
        if o and sid:
            mapa[o.group(1)] = sid.group(1)
    return mapa


def _num(txt):
    """'1,376.00' -> 1376.0 (el sitio usa coma de miles y punto decimal)."""
    return float(str(txt).replace(",", ""))


def parsear_ficha(html, producto_id, nombre):
    """Una fila por proveedor, con precio y días de entrega de cada región donde
    despacha (p_<id> / d_<id>)."""
    cfg = _config_json(html)
    hijos = _hijos_region(html)
    nombres = _proveedores_html(html)
    oferta_prov = _oferta_a_proveedor(html)
    dias = {}
    for seller, lst in cfg["rcArray"].items():
        for rc in lst:
            if str(rc.get("days_sp", "")).isdigit():
                dias[(seller, str(rc.get("region_id")))] = int(rc["days_sp"])
    por_prov = {}
    for rid, hijo in hijos.items():
        # jsonResult[hijo] = ofertas vigentes de esa región; las de qty 0 no se
        # muestran en la página (proveedor sin stock), así que tampoco acá.
        for oferta, d in cfg["jsonResult"].get(hijo, {}).items():
            seller = oferta_prov.get(oferta)
            try:
                qty, precio = _num(d.get("qty", 0)), _num(d["price"])
            except (TypeError, ValueError, KeyError):
                continue
            if seller is None or qty <= 0:
                continue
            fila = por_prov.setdefault(seller, {})
            clave = f"p_{rid}"
            fila[clave] = min(int(round(precio)), fila.get(clave, int(round(precio))))
            if (seller, rid) in dias:
                fila[f"d_{rid}"] = dias[(seller, rid)]
    filas = []
    for seller, vals in por_prov.items():
        nom, rut = nombres.get(seller, ("", ""))
        filas.append({"producto_id": producto_id, "producto": nombre, "proveedor_id": seller,
                      "proveedor": nom, "rut": rut, **vals})
    return filas


def a_largo(df):
    """Pasa el formato ancho (p_<id>/d_<id>) a una fila por producto-proveedor-región."""
    cols = COLS_BASE + ["region_id", "precio_neto", "dias_entrega"]
    if df.empty or f"p_{REGION_REF}" not in df.columns:
        return pd.DataFrame(columns=cols)
    partes = []
    for rid in REGIONES:
        pc, dc = f"p_{rid}", f"d_{rid}"
        if pc not in df.columns:
            continue
        hay = df[pc].notna()
        sub = df.loc[hay, COLS_BASE].copy()
        sub["region_id"] = rid
        sub["precio_neto"] = df.loc[hay, pc]
        sub["dias_entrega"] = df.loc[hay, dc] if dc in df.columns else pd.NA
        partes.append(sub)
    return pd.concat(partes, ignore_index=True)[cols] if partes else pd.DataFrame(columns=cols)


def _regiones_txt(ids):
    ids = set(ids)
    return ", ".join(REGIONES[r] for r in REGIONES if r in ids)


def calcular_cambios(anterior, actual, fecha):
    """Compara dos corridas y devuelve un DataFrame con qué cambió. Un cambio que
    afecta a varias regiones del mismo proveedor se junta en UNA fila."""
    la, lb = a_largo(anterior), a_largo(actual)
    if la.empty:  # primera corrida (o migración desde el formato antiguo): no hay con qué comparar
        return pd.DataFrame(columns=COLS_CAMBIOS)
    k = ["producto_id", "proveedor_id"]
    m = la.merge(lb, on=k + ["region_id"], how="outer", suffixes=("_a", "_b"), indicator=True)
    prods_ant, prods_act = set(la.producto_id), set(lb.producto_id)
    prov_ant = set(zip(la.producto_id, la.proveedor_id))
    prov_act = set(zip(lb.producto_id, lb.proveedor_id))
    solo_a, solo_b = m[m["_merge"] == "left_only"], m[m["_merge"] == "right_only"]
    ambos = m[m["_merge"] == "both"]
    camb = ambos[ambos["precio_neto_a"] != ambos["precio_neto_b"]]

    def nombre(r, col):
        v = getattr(r, col + "_b")
        return v if pd.notna(v) else getattr(r, col + "_a")

    filas = []

    def fila(tipo, pid, g, detalle):
        r = next(g.itertuples())
        filas.append({"fecha": fecha, "tipo": tipo, "producto_id": pid, "producto": nombre(r, "producto"),
                      "proveedor": nombre(r, "proveedor"), "rut": nombre(r, "rut"),
                      "regiones": _regiones_txt(g["region_id"]), "detalle": detalle})

    for (pid, sid), g in solo_b.groupby(k):
        if (pid, sid) not in prov_ant and pid in prods_ant:
            fila("proveedor nuevo", pid, g, f"desde ${int(g.precio_neto_b.min())} en {len(g)} región(es)")
        elif (pid, sid) in prov_ant:
            fila("nueva región", pid, g, f"ahora despacha a {len(g)} región(es) más")
    for (pid, sid), g in solo_a.groupby(k):
        if (pid, sid) not in prov_act and pid in prods_act:
            fila("proveedor retirado", pid, g, f"antes desde ${int(g.precio_neto_a.min())}")
        elif (pid, sid) in prov_act:
            fila("región retirada", pid, g, f"dejó de despachar a {len(g)} región(es)")
    orden = {r: i for i, r in enumerate(REGIONES)}
    for (pid, sid), g in camb.groupby(k):
        g = g.assign(_o=g["region_id"].map(orden)).sort_values("_o")
        partes = [f"{REGIONES[r.region_id]}: ${int(r.precio_neto_a)} → ${int(r.precio_neto_b)}"
                  for r in g.head(3).itertuples()]
        extra = len(g) - 3
        fila("cambio de precio", pid, g, "; ".join(partes) + (f"; y {extra} región(es) más" if extra > 0 else ""))
    for pid in prods_act - prods_ant:
        g = lb[lb.producto_id == pid]
        filas.append({"fecha": fecha, "tipo": "producto nuevo", "producto_id": pid,
                      "producto": g.iloc[0]["producto"], "proveedor": "", "rut": "", "regiones": "",
                      "detalle": f"{g.proveedor_id.nunique()} proveedores"})
    for pid in prods_ant - prods_act:
        filas.append({"fecha": fecha, "tipo": "producto retirado", "producto_id": pid,
                      "producto": la[la.producto_id == pid].iloc[0]["producto"], "proveedor": "",
                      "rut": "", "regiones": "", "detalle": ""})
    return pd.DataFrame(filas, columns=COLS_CAMBIOS)


def procesar_catalogo(clave, prueba=0):
    """Lee un catálogo completo y actualiza sus archivos. Devuelve True si salió bien;
    si la corrida no es confiable devuelve False y NO toca los datos anteriores."""
    cfg = CATALOGOS[clave]
    f_prov, f_cambios, f_estado = rutas(clave)
    inicio = datetime.now(TZ_CHILE) if TZ_CHILE else datetime.now()
    fecha = inicio.strftime("%d/%m/%Y %H:%M")
    productos = listar_productos(clave, prueba or None)
    log(f"{len(productos)} productos en el listado")

    filas, errores = [], []
    lock = threading.Lock()
    estado_run = {"hechos": 0, "seguidas": 0, "abortar": None}
    t0 = time.time()

    def leer(item):
        pid, nombre, url = item
        if estado_run["abortar"]:
            return
        try:
            res = parsear_ficha(_get(url), pid, nombre)
            with lock:
                filas.extend(res)
                estado_run["seguidas"] = 0
        except Exception as e:  # una ficha mala no tumba la corrida
            with lock:
                errores.append({"producto_id": pid, "producto": nombre, "error": str(e)[:200]})
                estado_run["seguidas"] += 1
                if estado_run["seguidas"] >= FALLAS_SEGUIDAS_MAX:
                    estado_run["abortar"] = "el sitio no responde desde este servidor"
        with lock:
            estado_run["hechos"] += 1
            n = estado_run["hechos"]
            if (time.time() - t0) / 60 > PRESUPUESTO_MIN:
                estado_run["abortar"] = f"se superaron los {PRESUPUESTO_MIN} min"
        if n % 25 == 0:
            log(f"  {n}/{len(productos)} ({(time.time() - t0) / 60:.1f} min, {len(errores)} errores)")
        time.sleep(PAUSA_SEG)

    with ThreadPoolExecutor(max_workers=HILOS) as pool:
        list(pool.map(leer, productos))
    abortada = estado_run["abortar"]
    if abortada:
        log(f"ABORTADA: {abortada} ({estado_run['hechos']}/{len(productos)} leídas)")
        errores.append({"producto_id": "", "producto": "", "error": f"abortada: {abortada}"})

    estado = {"ultima_corrida": fecha, "productos_listado": len(productos),
              "productos_con_error": len(errores), "errores": errores[:50], "ok": True}
    # Salvaguarda: si fallaron muchas fichas, algo cambió en el sitio; no se
    # pisan los datos buenos de la corrida anterior.
    if abortada or not filas or len(errores) > max(5, 0.2 * len(productos)):
        estado["ok"] = False
        f_estado.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")
        log("CORRIDA INVÁLIDA: no se actualizan los datos (se conservan los de la corrida anterior).")
        return False

    actual = pd.DataFrame(filas, columns=COLUMNAS)
    actual[COLS_PRECIO + COLS_DIAS] = actual[COLS_PRECIO + COLS_DIAS].astype("Int64")
    if prueba:
        actual.to_csv(DIR / f"{cfg['archivo']}_prueba.csv", index=False, encoding="utf-8-sig")
        log(actual[COLS_BASE[:2] + ["proveedor", f"p_{REGION_REF}", f"d_{REGION_REF}"]].head(8).to_string())
        log(f"{len(actual)} filas, {len(errores)} errores, "
            f"{int(actual[COLS_PRECIO].notna().sum().sum())} combinaciones proveedor-región")
        return True

    anterior = (pd.read_csv(f_prov, dtype=DTYPES_CSV) if f_prov.exists()
                else pd.DataFrame(columns=COLUMNAS))
    if not anterior.empty and f"p_{REGION_REF}" not in anterior.columns:
        log("Datos anteriores en formato antiguo (solo RM): se reemplazan y esta corrida no compara.")
    cambios = calcular_cambios(anterior, actual, fecha)
    if not cambios.empty:
        previos = (pd.read_csv(f_cambios, dtype=str) if f_cambios.exists()
                   else pd.DataFrame(columns=COLS_CAMBIOS))
        pd.concat([previos, cambios], ignore_index=True).to_csv(f_cambios, index=False, encoding="utf-8-sig")
    actual.to_csv(f_prov, index=False, encoding="utf-8-sig")
    estado["cambios_detectados"] = int(len(cambios))
    f_estado.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"Listo: {len(actual)} filas, {len(cambios)} cambios, {len(errores)} errores")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prueba", type=int, default=0, help="solo los primeros N productos de cada catálogo")
    ap.add_argument("--catalogo", choices=list(CATALOGOS) + ["todos"], default="todos")
    args = ap.parse_args()
    claves = list(CATALOGOS) if args.catalogo == "todos" else [args.catalogo]
    resultados = {}
    for clave in claves:  # un catálogo que falla no impide actualizar los demás
        log(f"=== {CATALOGOS[clave]['nombre']} ===")
        try:
            resultados[clave] = procesar_catalogo(clave, args.prueba)
        except Exception as e:
            log(f"ERROR en {clave}: {e}")
            resultados[clave] = False
    if not all(resultados.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
