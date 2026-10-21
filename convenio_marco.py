"""Monitor de proveedores de Convenio Marco (Mercado Público) — papel tissue.

Baja el listado de productos de la categoría y, de la ficha de cada uno, los
proveedores que despachan a la Región Metropolitana con su precio neto y días
de entrega. Compara con la corrida anterior y deja un resumen de qué cambió
(proveedores nuevos, retirados, cambios de precio, productos nuevos/retirados).

No usa API ni login: los datos vienen dentro del HTML público de la ficha
(bloque JSON "Webkul_MpAssignProduct/js/assignproduct" + filas de la tabla de
proveedores). Si Convenio Marco cambia ese bloque, el script falla fuerte y
avisa en estado_convenio_marco.json en vez de dejar datos a medias.

Uso:
    python convenio_marco.py              # corrida completa (~700 productos)
    python convenio_marco.py --prueba 3   # solo los primeros 3 productos
"""
import argparse
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
REGION_ID = "13"  # Región Metropolitana de Santiago
PAUSA_SEG = 1.0   # cortesía con el sitio: pausa de cada hilo entre fichas
HILOS = 4         # ~4 fichas en paralelo: ~700 fichas en ~4 min en vez de ~12
REINTENTOS = 2
TIMEOUT_SEG = 25
PRESUPUESTO_MIN = 30   # si la corrida pasa de esto se aborta limpia (el workflow corta a los 45)
FALLAS_SEGUIDAS_MAX = 15  # sitio caído/bloqueado: abortar rápido en vez de agotar el tiempo

DIR = Path(__file__).parent
ARCHIVO_ACTUAL = DIR / "convenio_marco_proveedores.csv"
ARCHIVO_CAMBIOS = DIR / "convenio_marco_cambios.csv"
ARCHIVO_ESTADO = DIR / "estado_convenio_marco.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (monitor-precios-dipisa)"}
COLUMNAS = ["producto_id", "producto", "proveedor_id", "proveedor", "rut",
            "precio_neto", "dias_entrega"]


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


def listar_productos(limite=None):
    """Recorre el listado (58 por página) y devuelve [(id, nombre, url)].
    Valida contra el total que declara la página ("N resultados")."""
    productos, vistos, pagina, total = [], {}, 1, None
    href = re.compile(r'href="(' + re.escape(LISTADO) + r'/[a-z0-9-]+-(\d+))"', re.I)
    nombre_re = re.compile(
        r'<a[^>]+href="' + re.escape(LISTADO) + r'/[a-z0-9-]+-(\d+)"[^>]*>\s*([^<]{5,}?)\s*</a>', re.I)
    while True:
        html = _get(LISTADO, {"p": pagina, "product_list_limit": 58})
        if total is None:
            m = re.search(r"de\s+(\d+)\s+resultados", html)
            total = int(m.group(1)) if m else None
        nombres = {pid: re.sub(r"\s+", " ", n).strip() for pid, n in nombre_re.findall(html)}
        nuevos = 0
        for url, pid in href.findall(html):
            if pid in vistos:
                continue
            nuevos += 1
            vistos[pid] = 1
            productos.append((pid, nombres.get(pid) or url.rsplit("/", 1)[-1], url))
        if nuevos == 0:
            break
        if limite and len(productos) >= limite:
            return productos[:limite]
        pagina += 1
        time.sleep(PAUSA_SEG)
    if total and len(productos) != total:
        print(f"AVISO: el sitio declara {total} productos y se leyeron {len(productos)}", file=sys.stderr)
    return productos


def _config_json(html):
    for m in re.finditer(r'<script type="text/x-magento-init">(.*?)</script>', html, re.S):
        if "Webkul_MpAssignProduct/js/assignproduct" in m.group(1):
            return json.loads(m.group(1))["body"]["Webkul_MpAssignProduct/js/assignproduct"]
    raise RuntimeError("no se encontró el bloque de proveedores (¿cambió el sitio?)")


def _hijo_region(html, region_id):
    """ID del producto-hijo que representa la región (la ficha es un producto
    configurable: cada región es una variante)."""
    m = re.search(r'"id":"%s","label":"[^"]*","products":\["(\d+)"\]' % region_id, html)
    if not m:
        raise RuntimeError(f"no se encontró la variante de la región {region_id}")
    return m.group(1)


def _proveedores_html(html):
    """{proveedor_id: (nombre, rut)} desde las filas de la tabla."""
    datos = {}
    for m in re.finditer(
            r'data-sellerid="(\d+)".*?data-name="([^"]*)"\s*data-rut="([^"]*)"', html, re.S):
        datos.setdefault(m.group(1), (m.group(2).strip(), m.group(3).strip()))
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
    cfg = _config_json(html)
    hijo = _hijo_region(html, REGION_ID)
    nombres = _proveedores_html(html)
    oferta_prov = _oferta_a_proveedor(html)
    dias_por_prov = {}
    for seller, lst in cfg["rcArray"].items():
        for rc in lst:
            if rc.get("region_id") == REGION_ID and str(rc.get("days_sp", "")).isdigit():
                dias_por_prov[seller] = int(rc["days_sp"])
    # jsonResult[hijo] = ofertas vigentes de esa región; las de qty 0 no se
    # muestran en la página (proveedor sin stock), así que tampoco acá.
    mejor = {}
    for oferta, d in cfg["jsonResult"].get(hijo, {}).items():
        seller = oferta_prov.get(oferta)
        try:
            qty, precio = _num(d.get("qty", 0)), _num(d["price"])
        except (TypeError, ValueError, KeyError):
            continue
        if seller is None or qty <= 0:
            continue
        mejor[seller] = min(precio, mejor.get(seller, precio))
    filas = []
    for seller, precio in mejor.items():
        nom, rut = nombres.get(seller, ("", ""))
        filas.append({
            "producto_id": producto_id, "producto": nombre, "proveedor_id": seller,
            "proveedor": nom, "rut": rut, "precio_neto": int(round(precio)),
            "dias_entrega": dias_por_prov.get(seller),
        })
    return filas


def calcular_cambios(anterior, actual, fecha):
    """Compara dos corridas y devuelve un DataFrame con qué cambió."""
    cambios = []
    if anterior.empty:
        return pd.DataFrame(columns=["fecha", "tipo", "producto_id", "producto",
                                     "proveedor", "rut", "detalle"])
    k = ["producto_id", "proveedor_id"]
    ant = anterior.set_index(k)
    act = actual.set_index(k)
    prods_ant, prods_act = set(anterior.producto_id), set(actual.producto_id)

    def fila(tipo, idx, r, detalle):
        cambios.append({"fecha": fecha, "tipo": tipo, "producto_id": idx[0],
                        "producto": r["producto"], "proveedor": r["proveedor"],
                        "rut": r["rut"], "detalle": detalle})

    for idx in act.index.difference(ant.index):
        if idx[0] in prods_ant:
            fila("proveedor nuevo", idx, act.loc[idx], f"${int(act.loc[idx, 'precio_neto'])}")
    for idx in ant.index.difference(act.index):
        if idx[0] in prods_act:
            fila("proveedor retirado", idx, ant.loc[idx], f"antes ${int(ant.loc[idx, 'precio_neto'])}")
    for idx in act.index.intersection(ant.index):
        a, b = int(ant.loc[idx, "precio_neto"]), int(act.loc[idx, "precio_neto"])
        if a != b:
            fila("cambio de precio", idx, act.loc[idx], f"${a} → ${b}")
    for pid in prods_act - prods_ant:
        r = actual[actual.producto_id == pid].iloc[0]
        fila("producto nuevo", (pid, ""), r, f"{(actual.producto_id == pid).sum()} proveedores en la RM")
    for pid in prods_ant - prods_act:
        r = anterior[anterior.producto_id == pid].iloc[0]
        fila("producto retirado", (pid, ""), r, "")
    return pd.DataFrame(cambios)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prueba", type=int, default=0, help="solo los primeros N productos")
    args = ap.parse_args()

    inicio = datetime.now()
    fecha = inicio.strftime("%d/%m/%Y %H:%M")
    productos = listar_productos(args.prueba or None)
    print(f"{len(productos)} productos en el listado")

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
        ARCHIVO_ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")
        log("CORRIDA INVÁLIDA: no se actualizan los datos (se conservan los de la corrida anterior).")
        sys.exit(1)

    actual = pd.DataFrame(filas, columns=COLUMNAS)
    if args.prueba:
        actual.to_csv(DIR / "convenio_marco_prueba.csv", index=False, encoding="utf-8-sig")
        print(actual.to_string(max_rows=60))
        print(f"{len(actual)} filas, {len(errores)} errores")
        return

    anterior = (pd.read_csv(ARCHIVO_ACTUAL, dtype={"producto_id": str, "proveedor_id": str})
                if ARCHIVO_ACTUAL.exists() else pd.DataFrame(columns=COLUMNAS))
    cambios = calcular_cambios(anterior, actual, fecha)
    if not cambios.empty:
        previos = (pd.read_csv(ARCHIVO_CAMBIOS, dtype=str)
                   if ARCHIVO_CAMBIOS.exists() else pd.DataFrame())
        pd.concat([previos, cambios], ignore_index=True).to_csv(
            ARCHIVO_CAMBIOS, index=False, encoding="utf-8-sig")
    actual.to_csv(ARCHIVO_ACTUAL, index=False, encoding="utf-8-sig")
    estado["cambios_detectados"] = int(len(cambios))
    ARCHIVO_ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Listo: {len(actual)} filas, {len(cambios)} cambios, {len(errores)} errores")


if __name__ == "__main__":
    main()
