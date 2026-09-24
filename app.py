import streamlit as st
import pandas as pd
import yaml
import json
import io
import re
import base64
import contextlib
import requests
from pathlib import Path

st.set_page_config(
    page_title="Monitor de Precios | Dipisa",
    page_icon="🧻",
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE_DIR = Path(__file__).parent

# Colores reales de la marca (sacados del logo, no un azul genérico) +
# paleta de estado fija (verde/rojo) que nunca se usa para identidad, solo
# para señalar "más barato" / "sin dato reciente" en las tablas.
COLOR_MORADO = "#4917A1"
COLOR_MORADO_OSCURO = "#2E0F66"
COLOR_VERDE = "#08C44E"
COLOR_TEXTO = "#1B252C"
COLOR_BUENO = "#0ca30c"
COLOR_CRITICO = "#d03b3b"


def _logo_base64():
    with open(BASE_DIR / "logo.png", "rb") as f:
        return base64.b64encode(f.read()).decode()


st.markdown(f"""
<style>
#MainMenu {{visibility: hidden;}}
footer {{visibility: hidden;}}
/* La barra superior se deja visible: contiene la flecha para plegar/desplegar
   la barra lateral de navegación. Solo se le baja el fondo para que no tape. */
header[data-testid="stHeader"] {{ background: transparent; }}

/* Barra lateral con tinte de marca */
section[data-testid="stSidebar"] {{
    background-color: #FBFAFE;
    border-right: 1px solid {COLOR_MORADO}1f;
}}
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {{ color: {COLOR_MORADO}; }}

/* Fondo entretenido: manchas suaves de los dos colores de marca en las
   esquinas, bien translúcidas para no restarle lectura a las tablas. */
.stApp {{
    background-color: #F7F6FB;
    background-image:
        radial-gradient(circle at 6% 10%, {COLOR_MORADO}33 0%, transparent 32%),
        radial-gradient(circle at 95% 15%, {COLOR_VERDE}2E 0%, transparent 28%),
        radial-gradient(circle at 12% 92%, {COLOR_VERDE}26 0%, transparent 30%),
        radial-gradient(circle at 96% 88%, {COLOR_MORADO}2E 0%, transparent 34%);
    background-attachment: fixed;
}}

/* Tarjetas con borde superior de marca en vez de gris genérico */
[data-testid="stVerticalBlockBorderWrapper"] {{
    border-top: 4px solid {COLOR_MORADO} !important;
    border-radius: 10px !important;
}}

/* Métricas con un tinte sutil de marca, no transparentes */
[data-testid="stMetric"] {{
    background-color: {COLOR_MORADO}0D;
    border: 1px solid {COLOR_MORADO}26;
    border-radius: 10px;
    padding: 10px 14px;
}}
[data-testid="stMetricValue"] {{ color: {COLOR_TEXTO}; }}

h2, h3 {{ color: {COLOR_MORADO}; }}
</style>
""", unsafe_allow_html=True)


HISTORIAL_PATH = BASE_DIR / "historial_precios.csv"
ESTADO_SCRAPER_PATH = BASE_DIR / "estado_scraper.json"
HISTORIAL_CATALOGO_PATH = BASE_DIR / "historial_catalogo.json"
OVERRIDES_CACHE_PATH = BASE_DIR / "url_overrides_cache.json"
CATALOGO_CACHE_PATH = BASE_DIR / "catalogo_cache.csv"

# Planilla de Google publicada (cuenta monitor.de.precios1@gmail.com).
# Pestaña "Arreglar Link" (sku_interno,url_nuevo,nota — reemplazar un URL
# muerto): todavía no existe en la planilla actual, función desactivada hasta
# que se cree esa pestaña y se publique (ver TRASPASO.md).
OVERRIDES_CSV_URL = ""
# Pestaña "Catálogo" (única pestaña de la planilla hoy): el catálogo COMPLETO,
# mismas columnas que productos.csv, una fila por producto (sin columna
# 'Acción': agregar/borrar/editar una fila es directamente eso).
CATALOGO_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTMZ7qyGdu79TJ5CUPN5dfIf4YZDgV9JqDpDdW8dA_jiqCrYDcW3RO_hGqjRp12QnKWKTvlkKvV1nWX/pub?gid=0&single=true&output=csv"
# Link para EDITAR la planilla (barra de direcciones al abrirla, termina en /edit).
# Si queda vacío, la app no muestra los botones que llevan a ella.
PLANILLA_EDIT_URL = "https://docs.google.com/spreadsheets/d/1Ka3EM2FEWd3uyfZ3CgzxeJhwQ9adETOvU0cihdPiBRw/edit"


@st.cache_data(ttl=600)
def cargar_overrides_url():
    """Correcciones de URL cargadas por el equipo en la planilla de Google. Si la
    planilla no responde, cae a la copia local del repo. Nunca rompe la app."""
    if not OVERRIDES_CSV_URL:
        return {}
    try:
        res = requests.get(OVERRIDES_CSV_URL, timeout=15)
        res.raise_for_status()
        df = pd.read_csv(io.StringIO(res.text)).rename(columns=ENCABEZADOS_URL_FIXES).dropna(subset=["sku_interno", "url_nuevo"])
        return {
            str(r["sku_interno"]).strip(): str(r["url_nuevo"]).strip()
            for _, r in df.iterrows()
            if str(r["url_nuevo"]).strip().startswith("http")
        }
    except Exception:
        if OVERRIDES_CACHE_PATH.exists():
            try:
                with open(OVERRIDES_CACHE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}


COLUMNAS_PRODUCTOS = [
    "sku_interno", "producto", "marca", "metros_totales", "retailer", "url",
    "categoria", "subcategoria", "rollos", "metros_rollo", "unidades",
]

# Encabezados en español que ve el equipo en la planilla de Google -> nombre
# interno que usa el código (debe ser igual al mismo mapa en scraper.py).
ENCABEZADOS_PRODUCTOS = {
    "Código": "sku_interno", "Producto": "producto",
    "Marca": "marca", "Retailer": "retailer", "Link": "url",
    "Categoría": "categoria", "Subcategoría": "subcategoria",
    "Rollos": "rollos", "Metros por rollo": "metros_rollo",
    "Metros totales": "metros_totales", "Unidades": "unidades",
    "Grupo": "grupo_id", "Nombre estándar": "nombre_estandar",
}
ENCABEZADOS_URL_FIXES = {"Código": "sku_interno", "Link nuevo": "url_nuevo", "Nota": "nota"}
COLUMNAS_NUMERICAS_PRODUCTO = ["metros_totales", "rollos", "metros_rollo", "unidades"]
COLUMNAS_TEXTO_OBLIGATORIAS = ["producto", "marca", "categoria", "subcategoria"]


@st.cache_data(ttl=600)
def cargar_catalogo_planilla():
    """Catálogo COMPLETO que el equipo mantiene en la pestaña 'Catálogo' de la
    planilla de Google: cada fila es un producto, sin columna 'Acción' — agregar
    una fila es un alta, borrarla es una baja, cambiar un valor es una edición.
    Si la planilla no responde, cae a la copia local. Nunca rompe la app. El
    scraper (scraper.py, cada corrida) es quien aplica estos cambios de verdad
    sobre productos.csv; acá solo se usan para el panel de validación y para
    mostrar un alta todavía no sincronizada como "🆕 provisorio"."""
    if not CATALOGO_CSV_URL:
        return pd.DataFrame(columns=COLUMNAS_PRODUCTOS)
    try:
        res = requests.get(CATALOGO_CSV_URL, timeout=15)
        res.raise_for_status()
        df = pd.read_csv(io.StringIO(res.text)).rename(columns=ENCABEZADOS_PRODUCTOS)
    except Exception:
        if CATALOGO_CACHE_PATH.exists():
            try:
                df = pd.read_csv(CATALOGO_CACHE_PATH).rename(columns=ENCABEZADOS_PRODUCTOS)
            except Exception:
                return pd.DataFrame(columns=COLUMNAS_PRODUCTOS)
        else:
            return pd.DataFrame(columns=COLUMNAS_PRODUCTOS)
    df["sku_interno"] = df.get("sku_interno", pd.Series(dtype=object)).astype(str).str.strip()
    df = df[(df["sku_interno"] != "") & (df["sku_interno"].str.lower() != "nan")]
    for col in COLUMNAS_PRODUCTOS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[COLUMNAS_PRODUCTOS]


def _numero_valido(val):
    """Igual que _parsear_numero en scraper.py: Google Sheets con configuración
    regional en español publica el CSV con coma decimal ('21,3'), así que hay
    que tolerar eso antes de reportar un valor numérico como inválido."""
    if pd.isna(val) or str(val).strip() == "":
        return True
    texto = str(val).strip()
    if pd.notna(pd.to_numeric(pd.Series([texto]), errors="coerce")).all():
        return True
    if "," in texto and "." not in texto:
        return pd.notna(pd.to_numeric(pd.Series([texto.replace(",", ".")]), errors="coerce")).all()
    return False


def _validar_catalogo_planilla(df, retailers_ok):
    """Misma validación fila por fila que hace el scraper (validar_catalogo en
    scraper.py) antes de aplicar la planilla — reimplementada acá para poder
    avisar en el dashboard SIN esperar a la próxima corrida automática (hasta
    ~8 h). Devuelve la lista de problemas encontrados, uno por fila con algo
    para revisar."""
    problemas = []
    vistos = set()
    for _, fila in df.iterrows():
        sku = str(fila["sku_interno"]).strip()
        if sku in vistos:
            problemas.append(f"**{sku}**: código repetido en la planilla, solo se usa la primera fila")
            continue
        vistos.add(sku)
        errs = []
        if str(fila.get("retailer", "")).strip() not in retailers_ok:
            errs.append(f"retailer '{fila.get('retailer')}' no es una clave válida")
        if not str(fila.get("url", "")).strip().startswith("http"):
            errs.append("falta el Link o no empieza con http")
        for col, nombre in zip(COLUMNAS_TEXTO_OBLIGATORIAS, ["Producto", "Marca", "Categoría", "Subcategoría"]):
            val = fila.get(col)
            if pd.isna(val) or not str(val).strip():
                errs.append(f"falta {nombre}")
        for col in COLUMNAS_NUMERICAS_PRODUCTO:
            val = fila.get(col)
            if not _numero_valido(val):
                errs.append(f"'{col}' = '{val}' no es un número válido")
        if errs:
            problemas.append(f"**{sku or '(sin código)'}**: " + "; ".join(errs))
    return problemas


@st.cache_data(ttl=600)
def cargar_historial():
    if not HISTORIAL_PATH.exists():
        return pd.DataFrame(columns=["semana", "sku_interno", "precio", "precio_normal", "fecha_act"])
    return pd.read_csv(HISTORIAL_PATH)


@st.cache_data(ttl=600)
def cargar_estado_scraper():
    if not ESTADO_SCRAPER_PATH.exists():
        return None
    with open(ESTADO_SCRAPER_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def cargar_historial_catalogo():
    """Altas/bajas/ediciones de catálogo detectadas en cada corrida (ver
    _registrar_cambios_catalogo en scraper.py), más recientes primero. El
    propio scraper ya poda las de más de 30 días, así que acá no hace falta
    filtrar de nuevo."""
    if not HISTORIAL_CATALOGO_PATH.exists():
        return []
    with open(HISTORIAL_CATALOGO_PATH, "r", encoding="utf-8") as f:
        historial = json.load(f)
    return list(reversed(historial))


@st.cache_data(ttl=600)
def cargar_datos():
    with open(BASE_DIR / "datos_procesados.json", "r", encoding="utf-8") as f:
        datos = json.load(f)
    df_json = pd.DataFrame(datos)

    df_csv = pd.read_csv(BASE_DIR / "productos.csv", comment="#", skip_blank_lines=True)
    df_csv = df_csv.dropna(subset=["sku_interno"])
    df_csv["origen_planilla"] = False

    with open(BASE_DIR / "retailers.yaml", "r", encoding="utf-8") as f:
        retailers_cfg = yaml.safe_load(f)

    # Altas cargadas por el equipo en la pestaña 'Catálogo' de la planilla que
    # todavía no llegaron a productos.csv: se muestran como cualquier otro
    # producto pero marcadas "🆕 provisorio" hasta que el scraper las aplique en
    # su próxima corrida (sincronizar_desde_catalogo en scraper.py). Ediciones y
    # bajas hechas en la planilla solo se ven reflejadas recién ahí también, no
    # acá — evita mostrar en vivo un cambio que el scraper todavía podría
    # rechazar (ver validar_catalogo). Solo se muestran altas que ya pasan la
    # validación básica, para no ensuciar el dashboard con una fila rota.
    catalogo_planilla = cargar_catalogo_planilla()
    nuevos = catalogo_planilla[~catalogo_planilla["sku_interno"].isin(df_csv["sku_interno"])]
    if not nuevos.empty:
        retailers_ok = set(retailers_cfg.keys())
        valido = (
            nuevos["retailer"].astype(str).str.strip().isin(retailers_ok)
            & nuevos["url"].astype(str).str.strip().str.startswith("http")
            & nuevos["producto"].notna() & nuevos["marca"].notna()
            & nuevos["categoria"].notna() & nuevos["subcategoria"].notna()
        )
        nuevos = nuevos[valido].drop_duplicates("sku_interno").copy()
    if not nuevos.empty:
        nuevos["origen_planilla"] = True
        df_csv = pd.concat([df_csv, nuevos], ignore_index=True)

    # URLs corregidos a mano por el equipo desde la planilla de Google — se
    # aplican encima del CSV para que el "Ver ↗" apunte al link vigente.
    overrides = cargar_overrides_url()
    df_csv["url_corregido"] = df_csv["sku_interno"].isin(list(overrides))
    df_csv["url"] = df_csv.apply(
        lambda r: overrides.get(r["sku_interno"], r["url"]), axis=1
    )

    # LEFT join (no inner): un producto sin precio todavía (recién agregado,
    # o que lleva varias corridas fallando) sigue apareciendo como
    # "Pendiente" en vez de desaparecer en silencio de la página.
    df = pd.merge(df_csv, df_json, on="sku_interno", how="left")
    df["retailer_nombre"] = df["retailer"].map(lambda r: retailers_cfg.get(r, {}).get("nombre", r))
    # canal = "retail" (supermercados) o "mayorista" (distribuidores que venden
    # por manga/caja). Se define en retailers.yaml; sin la clave se asume retail.
    # Sirve para no mezclar los dos mundos en la comparación por segmento.
    df["canal"] = df["retailer"].map(lambda r: retailers_cfg.get(r, {}).get("canal", "retail"))
    # Retailers pausados (ver retailers.yaml, ej. Knasta bloqueado) siguen
    # visibles en el dashboard con su último precio conocido, pero no van en
    # el CSV descargable — no tiene sentido exportar un dato que no se está
    # actualizando y podría confundir en una planilla.
    df["retailer_desactivado"] = df["retailer"].map(lambda r: bool(retailers_cfg.get(r, {}).get("deshabilitado")))

    df["rollos"] = pd.to_numeric(df.get("rollos"), errors="coerce")
    df["metros_rollo"] = pd.to_numeric(df.get("metros_rollo"), errors="coerce")
    df["metros_totales"] = pd.to_numeric(df["metros_totales"], errors="coerce")
    df["unidades"] = pd.to_numeric(df.get("unidades"), errors="coerce")
    es_serv = df["categoria"] == "Servilletas"
    # Red para filas de rollo (PH/toallas) que llegaron incompletas (ej. de la
    # planilla productos_nuevos): rollos/metros_rollo se intentan sacar del
    # nombre, y con eso se completa metros_totales. Las servilletas no tienen
    # rollos ni metros — se comparan por unidades — así que se saltan.
    faltan = (df["rollos"].isna() | df["metros_rollo"].isna()) & ~es_serv
    if faltan.any():
        parsed = df.loc[faltan, "producto"].map(_parse_rollos_metros)
        df.loc[faltan, "rollos"] = df.loc[faltan, "rollos"].fillna(parsed.map(lambda t: t[0]))
        df.loc[faltan, "metros_rollo"] = df.loc[faltan, "metros_rollo"].fillna(parsed.map(lambda t: t[1]))
    sin_mt = df["metros_totales"].isna() & df["rollos"].notna() & df["metros_rollo"].notna()
    df.loc[sin_mt, "metros_totales"] = df.loc[sin_mt, "rollos"] * df.loc[sin_mt, "metros_rollo"]
    df["segmento"] = df.apply(_segmento, axis=1)

    # Formato mayorista: algunos retailers (Central Mayorista, a veces Alvi)
    # venden el pack de góndola dentro de una "manga"/"caja" de N packs y
    # publican el precio de esa manga. Para comparar contra un pack suelto hay
    # que bajar todo a $/pack. N sale de la columna 'unidades' (si la cargaron
    # a mano en un PH/toalla) o se lee del nombre ("MANGAx12"). Las servilletas
    # usan 'unidades' para otra cosa (el conteo real del pack) y quedan afuera.
    packs_col = pd.to_numeric(df["unidades"], errors="coerce").where(~es_serv)
    packs_nom = pd.to_numeric(df["producto"].map(_packs_por_bulto), errors="coerce").where(~es_serv)
    df["packs_por_bulto"] = packs_col.fillna(packs_nom)
    # Servilletas: 'unidades' es el conteo real del pack, así que el
    # multiplicador de caja (N paquetes por caja mayorista) se carga en la
    # columna 'metros_totales' — que en servilletas no se usa para nada más.
    packs_serv = pd.to_numeric(df["metros_totales"], errors="coerce").where(es_serv)
    df.loc[es_serv, "packs_por_bulto"] = packs_serv
    df.loc[es_serv, "metros_totales"] = pd.NA
    hay_bulto = df["packs_por_bulto"].fillna(1) > 1

    df["precio"] = pd.to_numeric(df["precio"], errors="coerce")
    df["precio_normal"] = pd.to_numeric(df["precio_normal"], errors="coerce")
    # Solo Alvi trae este campo (precio socio comprando 2+ unidades) — no
    # existe como columna si aún no hay ningún SKU de Alvi con datos.
    df["precio_socio2"] = pd.to_numeric(df["precio_socio2"], errors="coerce") if "precio_socio2" in df.columns else pd.NA
    # precio_ref = métrica con la que se compara y se resalta el más barato:
    # $/metro para papel higiénico y toalla, $/unidad para servilletas. La
    # categoría manda: un valor cargado en la columna "equivocada" (ej.
    # 'unidades' en un papel higiénico) no cambia la métrica, se ignora.
    # precio_manga = lo que se scrapea cuando es un envase colectivo (NA si no).
    # precio_pack = precio de un pack suelto: si es manga, se divide por N.
    df["precio_manga"] = df["precio"].where(hay_bulto)
    _div = df["packs_por_bulto"].where(hay_bulto, 1)
    df["precio_pack"] = (df["precio"] / _div).round(0)
    df["precio_pack_normal"] = (df["precio_normal"] / _div).round(0)

    df["precio_metro"] = (df["precio_pack"] / df["metros_totales"]).round(1)
    df.loc[es_serv, "precio_metro"] = pd.NA
    # $/metro con el precio mayorista "desde 2 unidades" (ej. Liquimax) — se
    # muestra al lado del $/metro de lista para comparar los dos escenarios.
    _p2 = pd.to_numeric(df["precio_socio2"], errors="coerce")
    df["precio_metro_2un"] = (_p2 / df["metros_totales"]).round(1)
    df.loc[es_serv, "precio_metro_2un"] = pd.NA
    # $/unidad de servilletas: sobre el precio de un pack suelto (si venía en
    # caja mayorista, precio_pack ya lo dividió por los N paquetes).
    df["precio_unidad"] = (df["precio_pack"] / df["unidades"]).round(1)
    df.loc[~es_serv, "precio_unidad"] = pd.NA
    df["precio_ref"] = df["precio_metro"]
    df.loc[es_serv, "precio_ref"] = df.loc[es_serv, "precio_unidad"]
    df["ref_unidad"] = "m"
    df.loc[es_serv, "ref_unidad"] = "u"

    descuento = (1 - df["precio"] / df["precio_normal"]) * 100
    df["descuento_pct"] = descuento.round(0)
    df.loc[df["descuento_pct"] <= 0, "descuento_pct"] = pd.NA

    df["estado"] = df["estado"].fillna("Pendiente")

    ovella_df = pd.read_csv(BASE_DIR / "ovella.csv", comment="#", skip_blank_lines=True)
    ovella_df = ovella_df.dropna(subset=["sku_ovella"])
    ovella_df["segmento"] = ovella_df.apply(_segmento, axis=1)

    return df, ovella_df


def _formatear_clp(valor):
    if pd.isna(valor):
        return "N/D"
    return f"${valor:,.0f}".replace(",", ".")


# --- Segmento competitivo -------------------------------------------------
# Dos productos compiten de verdad cuando el comprador elige entre ellos en la
# góndola: mismo tipo de hoja y mismo formato de pack (rollos x metros). Esta
# lista de "sectores" la define el equipo comercial. Cada entrada es
# (rollos_aceptados, metros_min, metros_max, etiqueta); un producto entra al
# primer sector cuyo nº de rollos coincida y cuyos metros/rollo caigan en el
# rango. Los rangos son flexibles a propósito (no "50 exacto" sino ~44-56) para
# no dejar afuera formatos vecinos. Agregar/ajustar sectores acá.
SECTORES = [
    ({4}, 44, 56, "4 x 50 mt"),      ({8, 9, 10}, 35, 56, "8 x 50 mt"),
    ({11, 12, 13}, 35, 58, "12 x 50 mt"), ({16, 17, 18}, 35, 58, "18 x 50 mt"),
    ({4}, 35, 46, "4 x 40 mt"),      ({24}, 35, 46, "24 x 40 mt"),
    ({8}, 23, 34, "8 x 30 mt"),      ({4}, 26, 34, "4 x 30 mt"),
    ({6}, 28, 34, "6 x 32 mt"),
    ({11, 12}, 24, 30, "12 x 27 mt"), ({18}, 24, 30, "18 x 27 mt"),
    ({4}, 23, 27, "4 x 25 mt"),
    ({24}, 18, 27, "24 x 22 mt"),    ({16, 17}, 18, 27, "16 x 22 mt"),
    ({32, 40}, 19, 27, "40 x 22 mt"), ({6}, 19, 27, "6 x 22 mt"),
    ({11, 12}, 15, 23, "12 x 22 mt"), ({4}, 18, 23, "4 x 22 mt"),
    ({6}, 17, 19, "6 x 18 mt"),      ({4}, 17, 19, "4 x 18 mt"),
    ({6}, 14, 16, "6 x 16 mt"),
    ({12}, 10, 15, "12 x 12 mt"),    ({3}, 10, 15, "3 x 12 mt"),
    ({2}, 10, 16, "2 x 14 mt"),      ({2}, 17, 22, "2 x 20 mt"),
    ({2}, 23, 25, "2 x 24 mt"),      ({2}, 26, 40, "2 x 26 mt"),
    ({1}, 20, 38, "1 x 26 mt"),
    ({1}, 40, 52, "1 x 45 mt"),      ({1}, 60, 75, "1 x 70 mt"),
    ({1}, 76, 95, "1 x 80 mt"),      ({1}, 96, 130, "1 x 100 mt"),
    ({1}, 131, 170, "1 x 150 mt"),
    ({8, 10}, 90, 130, "8 x 110 mt"), ({4}, 90, 130, "4 x 100 mt"),
    ({2}, 50, 70, "2 x 60 mt"),
]


def _sector(rollos, metros_rollo):
    try:
        r = int(rollos)
        m = float(metros_rollo)
    except (TypeError, ValueError):
        return None
    for rols, mn, mx, lab in SECTORES:
        if r in rols and mn <= m <= mx:
            return lab
    return None


# Servilletas: se agrupan solo por tamaño de pack (cantidad de unidades), sin
# tipo ni hoja. Rangos flexibles, misma idea que los sectores de papel.
SECTORES_SERVILLETAS = [
    (0, 25, "20 un"), (26, 90, "50 un"), (91, 190, "150 un"),
    (191, 235, "200 un"), (236, 350, "300 un"), (351, 99999, "400 un"),
]


def _sector_servilleta(unidades):
    try:
        u = float(unidades)
    except (TypeError, ValueError):
        return None
    for mn, mx, lab in SECTORES_SERVILLETAS:
        if mn <= u <= mx:
            return lab
    return None


def _parse_rollos_metros(nombre):
    """Saca (rollos, metros_por_rollo) del nombre del producto — mismos patrones
    que se usaron para rellenar productos.csv ("22 m 4 un", "4 un 22 m",
    "2 x 26 mts"). Sirve de red para las filas de la planilla 'productos_nuevos'
    a las que alguien no les cargó rollos/metros_rollo a mano."""
    s = str(nombre).lower().replace("mts", "m").replace("metros", "m")
    m3 = re.search(r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*m", s)
    if m3:
        return int(m3.group(1)), float(m3.group(2))
    m1 = re.search(r"(\d+(?:\.\d+)?)\s*m\b.*?(\d+)\s*un", s)
    if m1:
        return int(m1.group(2)), float(m1.group(1))
    m2 = re.search(r"(\d+)\s*un.*?(\d+(?:\.\d+)?)\s*m\b", s)
    if m2:
        return int(m2.group(1)), float(m2.group(2))
    return None, None


def _packs_por_bulto(nombre):
    """Cuántos packs de góndola trae una 'manga'/'caja'/'bulto' cuando un
    mayorista publica el precio del envase colectivo (ej. Central Mayorista
    "... CONFORT MANGAx12"). Devuelve None si es un pack suelto. Es solo una
    red: el valor firme se carga en la columna 'unidades' de productos.csv."""
    m = re.search(r"(?:manga|caja|bulto|display)\s*x?\s*(\d+)\b", str(nombre), re.I)
    return int(m.group(1)) if m else None


def _segmento(row):
    # Servilletas: se agrupan solo por rango de unidades (el tipo Cocktail/Mesa
    # queda visible en la columna Formato pero no arma el segmento). Excepción:
    # las de dispensador son de uso institucional (papel más delgado, formato
    # interfoliado) y no compiten con las de mesa/cóctel — van a su propio
    # segmento.
    if row.get("categoria") == "Servilletas":
        if row.get("subcategoria") == "Dispensador":
            sec = _sector_servilleta(row.get("unidades"))
            return f"Servilletas Dispensador · {sec}" if sec else "Servilletas Dispensador"
        sec = _sector_servilleta(row.get("unidades"))
        return f"Servilletas · {sec}" if sec else None
    if pd.isna(row.get("rollos")) or pd.isna(row.get("metros_rollo")):
        return None
    sub = row["subcategoria"]
    # Triple hoja de ~40 m: son pocos SKU, se juntan todos en un solo segmento
    # sin importar el tamaño del pack (4, 8 o 12 rollos).
    if sub == "Triple Hoja" and 34 <= float(row["metros_rollo"]) <= 47:
        return "Triple Hoja · 40 mt"
    sec = _sector(row["rollos"], row["metros_rollo"])
    if sec is None:
        return None
    # El tipo de hoja va en la etiqueta para no mezclar doble hoja con hoja
    # simple ni triple hoja en un mismo sector.
    return f"{sub} · {sec}"


# Abreviación de categoría al estilo de la planilla "Formato Toma Precios"
# del jefe (columna "Cat."): Hig / Toa / Ser.
CAT_ABREVIADA = {"Papel Higienico": "Hig", "Toalla de Papel": "Toa", "Servilletas": "Ser"}

# Fabricante real detrás de cada marca (clasificación del jefe, no del scraper
# — varias marcas "propias" de un retailer o de un fabricante chico comparten
# dueño real, ej. Softys fabrica Confort/Elite/Noble/Nova/Abolengo/Rendipel).
# Marca sin entrada acá -> "No identificado", no se inventa un fabricante.
FABRICANTE_POR_MARCA = {
    "Abolengo": "Softys",
    "Acuenta": "MP",
    "Affecto": "No identificado",
    "Bless": "No identificado",
    "Confort": "Softys",
    "Don Aurelio": "No identificado",
    "Elite": "Softys",
    "Family Care": "MP",
    "Favorita": "Essity",
    "Florax": "No identificado",
    "Generico": "No identificado",
    "Giulietta": "No identificado",
    "Home Care": "MP",
    "Lider": "MP",
    "Máxima": "FPC",
    "Merkat": "MP",
    "Noble": "Softys",
    "Nova": "Softys",
    "Nubelin": "MP",
    "Ovella": "Dipisa",
    "Pilucho": "No identificado",
    "Rendipel": "Softys",
    "Scott": "Kimberly-Clark",
    "Smart Price": "MP",
    "Swan": "FPC",
    "Today": "No identificado",
    "Tork": "Essity",
    "Tottus": "MP",
    "Xplend": "MP",
}


def _fabricante(marca):
    return FABRICANTE_POR_MARCA.get(str(marca).strip(), "No identificado")


def _sector_plano(segmento):
    """De 'Doble Hoja · 4 x 50 mt' saca solo '4 x 50 mt' — el formato de pack
    sin el tipo de hoja, que es la columna "Sector" de la planilla del jefe."""
    if not segmento or pd.isna(segmento):
        return ""
    return str(segmento).split("·", 1)[-1].strip()


def _armar_export_formato_jefe(df_export):
    """Reproduce exactamente la estructura de columnas de la planilla
    "Formato Toma Precios" que pasó el jefe (Codigo/Fecha/D/M/A/Tipo/Local/
    Orden/Cat./Fabrica/Sector/Cod/Descripción/Un x Bulto/Un x Pqte./Rollos
    bulto/Mt x Rollo/Mt x Bulto/PVP Bulto/PVP Pqte/$ x Mt), para que se pueda
    usar como reemplazo directo de la toma de precios manual. La diferencia:
    en la planilla del jefe las columnas de precio vienen vacías (se llenan a
    mano); acá ya vienen con el precio scrapeado.

    "Fabrica" es el fabricante real (FABRICANTE_POR_MARCA, ej. Softys detrás
    de Confort/Elite/Noble), no la marca — la marca comercial va en la
    columna "Marca", agregada aparte.

    Mapeo de columnas de pack a nuestro modelo de datos (confirmado contra
    filas reales de la planilla del jefe, incluida una de servilletas):
    - "Un x Bulto"   = packs por manga/caja (packs_por_bulto, 1 si es suelto)
    - "Un x Pqte."   = rollos por pack (o 'unidades' del pack en Servilletas)
    - "Rollos bulto" = rollos totales en el bulto (Un x Bulto × Un x Pqte);
                        en Servilletas el jefe repite ahí el mismo valor de
                        "Un x Bulto" en vez de multiplicar, así que se replica
                        igual para que la estructura calce.
    - "Mt x Rollo"   = metros por rollo (o el conteo de unidades del pack en
                        Servilletas, mismo dato que "Un x Pqte.")
    - "Mt x Bulto"   = metros totales del bulto (o unidades totales del
                        bulto en Servilletas)
    - "PVP Bulto"    = precio de la manga/caja completa (vacío si es pack
                        suelto, igual que en la planilla del jefe)
    - "PVP Pqte"     = precio del pack suelto (ya es el precio vigente/más
                        bajo: 'precio' trae la oferta cuando existe)
    - "Precio Lista" = precio_pack_normal (precio de lista del pack, antes
                        de descuento; vacío si el retailer no informa lista)
    - "Precio Oferta"= igual a "PVP Pqte" pero solo cuando hay descuento
                        vigente (vacío si no hay oferta, para que se note)
    - "Descuento %"  = descuento_pct
    - "$ x Mt"        = precio_ref ($/metro en PH y Toalla, $/unidad en
                        Servilletas), calculado siempre sobre el precio
                        vigente/más bajo (PVP Pqte), nunca sobre el de lista
                        — el jefe reusa la misma columna)
    "Tipo" sale del canal (retailers.yaml): 'May' si es mayorista, 'Ret' si
    es retail — el único valor que trae la planilla de ejemplo es 'May'
    (aCuenta), así que 'Ret' es una extensión razonable, a confirmar con el
    jefe. "Orden" es el correlativo dentro de cada Local, en el mismo orden
    en que ya vienen las filas."""
    es_serv = df_export["categoria"] == "Servilletas"
    bulto = pd.to_numeric(df_export.get("packs_por_bulto"), errors="coerce").fillna(1)

    un_x_pqte = df_export["rollos"].where(~es_serv, df_export["unidades"])
    rollos_bulto = (bulto * df_export["rollos"]).where(~es_serv, bulto)
    mt_x_rollo = df_export["metros_rollo"].where(~es_serv, df_export["unidades"])
    mt_x_bulto = (bulto * df_export["metros_totales"]).where(~es_serv, bulto * df_export["unidades"])

    filas = []
    for _, r in df_export.iterrows():
        local = r["retailer_nombre"]
        cat = CAT_ABREVIADA.get(r["categoria"], "")
        sector = _sector_plano(r.get("segmento"))
        fecha = pd.to_datetime(r.get("fecha_act"), format="%d/%m/%Y %H:%M hrs", errors="coerce")
        i = r.name
        filas.append({
            "Codigo": r["sku_interno"],
            "Fecha": fecha.date() if pd.notna(fecha) else "",
            "D": fecha.day if pd.notna(fecha) else "",
            "M": fecha.month if pd.notna(fecha) else "",
            "A": fecha.year if pd.notna(fecha) else "",
            "Tipo": "May" if r.get("canal") == "mayorista" else "Ret",
            "Local": local,
            "Orden": _num_grupo(r),
            "Cat.": cat,
            "Fabrica": _fabricante(r["marca"]),
            "Marca": r["marca"],
            "Sector": sector,
            "Cod": f"{cat} {sector}".strip() if sector else "",
            "Descripción": _producto_estandar(r),
            "Un x Bulto": int(bulto[i]) if bulto[i] > 1 else "",
            "Un x Pqte.": "" if pd.isna(un_x_pqte[i]) else int(un_x_pqte[i]),
            "Rollos bulto": "" if pd.isna(rollos_bulto[i]) else int(rollos_bulto[i]),
            "Mt x Rollo": "" if pd.isna(mt_x_rollo[i]) else mt_x_rollo[i],
            "Mt x Bulto": "" if pd.isna(mt_x_bulto[i]) else mt_x_bulto[i],
            "PVP Bulto": int(r["precio_manga"]) if pd.notna(r.get("precio_manga")) else "",
            "PVP Pqte": int(r["precio_pack"]) if pd.notna(r.get("precio_pack")) else "",
            "Precio Lista": int(r["precio_pack_normal"]) if pd.notna(r.get("precio_pack_normal")) else "",
            "Precio Oferta": int(r["precio_pack"]) if pd.notna(r.get("descuento_pct")) else "",
            "Descuento %": f"{int(r['descuento_pct'])}%" if pd.notna(r.get("descuento_pct")) else "",
            "$ x Mt": r["precio_ref"] if pd.notna(r.get("precio_ref")) else "",
        })
    df_out = pd.DataFrame(filas)
    if df_out.empty:
        return df_out
    # Reordena como en la planilla del jefe: filas agrupadas por Sector (en el
    # orden en que aparecen, sin resortear alfabéticamente) y, dentro de cada
    # grupo, Ovella primero y después la competencia — así el Excel queda
    # listo para el resaltado por grupo que se aplica al exportar.
    grupo_orden = df_out.groupby("Sector", sort=False).ngroup()
    es_competencia = df_out["Marca"].astype(str).str.strip().str.lower() != "ovella"
    orden = pd.DataFrame({"_g": grupo_orden, "_c": es_competencia}).assign(_i=range(len(df_out)))
    idx = orden.sort_values(["_g", "_c", "_i"], kind="stable").index
    return df_out.loc[idx].reset_index(drop=True)


def _aplicar_formato_jefe(ws, df_out):
    """Aplica al sheet el estilo visual de la planilla "Formato Toma Precios"
    del jefe: encabezado amarillo en negrita, franjas celestes alternadas por
    grupo de Sector (para separar visualmente cada formato de pack) y la fila
    más barata de cada grupo resaltada en verde — mismo criterio de "más
    barato" que ya se usa en las tablas de pantalla (COLOR_BUENO)."""
    from openpyxl.styles import Font, PatternFill, Border, Side, Alignment

    AMARILLO = PatternFill("solid", fgColor="FFFF00")
    CELESTE = PatternFill("solid", fgColor="DCE6F1")
    VERDE = PatternFill("solid", fgColor="C6EFCE")
    borde_fino = Side(style="thin", color="BFBFBF")
    BORDE = Border(left=borde_fino, right=borde_fino, top=borde_fino, bottom=borde_fino)

    n_cols = len(df_out.columns)
    for col in range(1, n_cols + 1):
        c = ws.cell(row=1, column=col)
        c.fill = AMARILLO
        c.font = Font(bold=True)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDE
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    # Grupos de Sector consecutivos (ya vienen agrupados por _armar_export_formato_jefe).
    grupo_actual = None
    grupo_num = -1
    celeste_on = False
    inicio_grupo = 2
    for fila_i, valor_sector in enumerate(df_out["Sector"], start=2):
        if valor_sector != grupo_actual:
            grupo_actual = valor_sector
            grupo_num += 1
            celeste_on = grupo_num % 2 == 1
            inicio_grupo = fila_i
        if celeste_on:
            for col in range(1, n_cols + 1):
                ws.cell(row=fila_i, column=col).fill = CELESTE
        for col in range(1, n_cols + 1):
            ws.cell(row=fila_i, column=col).border = BORDE

    # Fila más barata ($ x Mt mínimo, ignorando vacíos) dentro de cada grupo.
    for _, idx in df_out.groupby((df_out["Sector"] != df_out["Sector"].shift()).cumsum()).groups.items():
        precios = pd.to_numeric(df_out.loc[idx, "$ x Mt"], errors="coerce")
        if precios.notna().any():
            fila_min = precios.idxmin() + 2  # +2: header + índice base 0 -> fila Excel
            for col in range(1, n_cols + 1):
                ws.cell(row=fila_min, column=col).fill = VERDE

    for col_i, nombre in enumerate(df_out.columns, start=1):
        ancho = max(len(str(nombre)), df_out[nombre].astype(str).str.len().max() if len(df_out) else 0)
        ws.column_dimensions[ws.cell(row=1, column=col_i).column_letter].width = min(max(ancho + 2, 8), 40)


def _armar_export(df_export):
    """Versión "para humanos" del dataframe interno, pensada para abrirse en
    Excel: nombres de columna en español, precios ya formateados en CLP en
    vez de floats crudos, y sin las columnas internas (sku_interno, index de
    merge, etc.) que no significan nada fuera de la app."""
    filas = []
    for _, r in df_export.iterrows():
        cat = CAT_ABREVIADA.get(r["categoria"], "")
        sector = _sector_plano(r.get("segmento"))
        filas.append({
            "Grupo": _num_grupo(r),
            "Retailer": r["retailer_nombre"],
            "Categoría": r["categoria"],
            "Cat": cat,
            "Subcategoría": r["subcategoria"],
            "Fabrica": _fabricante(r["marca"]),
            "Sector": sector,
            "Cod": f"{cat} {sector}".strip() if sector else "",
            "Segmento": r.get("segmento") or "",
            "Producto estándar": _producto_estandar(r),
            "Marca": r["marca"],
            "Producto": r["producto"],
            "Metros totales": "" if pd.isna(r.get("metros_totales")) else r["metros_totales"],
            "Unidades": int(r["unidades"]) if r["categoria"] == "Servilletas" and pd.notna(r.get("unidades")) else "",
            "Packs por manga/caja": int(r["packs_por_bulto"]) if pd.notna(r.get("packs_por_bulto")) and r["packs_por_bulto"] > 1 else "",
            "Precio manga/caja": _formatear_clp(r.get("precio_manga")) if pd.notna(r.get("precio_manga")) else "",
            "Precio pack": _formatear_clp(r.get("precio_pack")) if pd.notna(r.get("precio_pack")) else "",
            "Precio Lista": _formatear_clp(r["precio_normal"]),
            "Precio Oferta": _formatear_clp(r["precio"]) if pd.notna(r["descuento_pct"]) else "",
            "Descuento %": f"{int(r['descuento_pct'])}%" if pd.notna(r["descuento_pct"]) else "",
            "Precio 2+ un (mayorista)": _formatear_clp(r.get("precio_socio2")) if pd.notna(r.get("precio_socio2")) else "",
            "$/Metro 2+ un": f"${r['precio_metro_2un']}/m" if pd.notna(r.get("precio_metro_2un")) else "",
            "$/Metro o $/unidad": f"${r['precio_ref']}/{r.get('ref_unidad', 'm')}" if pd.notna(r.get("precio_ref")) else "N/D",
            "Estado": r["estado"],
            "Última actualización": r.get("fecha_act") or "",
            "Link": r.get("url"),
        })
    return pd.DataFrame(filas)


def _fmt_formato(r):
    if r.get("categoria") == "Servilletas" and pd.notna(r.get("unidades")):
        base = f"{int(r['unidades'])} un · {r['subcategoria']}"
        n = r.get("packs_por_bulto")
        if pd.notna(n) and n and n > 1:
            base += f" · caja x{int(n)}"
        return base
    if pd.notna(r.get("rollos")) and pd.notna(r.get("metros_rollo")):
        base = f"{int(r['rollos'])}x{r['metros_rollo']:g}m · {r['subcategoria']}"
        n = r.get("packs_por_bulto")
        if pd.notna(n) and n and n > 1:
            base += f" · manga x{int(n)}"
        return base
    return f"{r['categoria']} · {r['subcategoria']}"


def _fmt_grupo(r):
    """Nº de grupo del cruce manual (ver grupo_id en productos.csv) — permite
    ubicar 'todos los del grupo 9' entre retailers. '—' si el SKU todavía no
    fue cruzado. Para tablas en pantalla (texto); en los Excel exportados se
    usa _num_grupo para que la columna quede numérica y se pueda ordenar."""
    g = r.get("grupo_id")
    if pd.isna(g):
        return "—"
    try:
        return str(int(float(g)))
    except (TypeError, ValueError):
        return str(g)


def _num_grupo(r):
    """Igual que _fmt_grupo pero devuelve un número (celda vacía "" si no
    tiene grupo) en vez de texto con '—', para que "Orden"/"Grupo" salgan
    como celdas numéricas de verdad en los Excel/CSV exportados y se puedan
    ordenar ascendente/descendente (mismo criterio que el resto de columnas
    numéricas de esta exportación, ej. "PVP Pqte"). Todos los grupo_id de
    productos.csv son numéricos (se renumeraron los que eran texto); si en
    el futuro se carga uno nuevo con texto, cae a "" en vez de romper la
    exportación."""
    g = r.get("grupo_id")
    if pd.isna(g):
        return ""
    try:
        return int(float(g))
    except (TypeError, ValueError):
        return ""


def _producto_estandar(r):
    """Nombre idéntico para el mismo producto en cualquier retailer, para
    comparar precio entre retailers (ej. en una tabla dinámica de Excel).
    Prioridad: la columna 'nombre_estandar' de productos.csv, cargada a mano
    (cruce de fichas reales, confirma qué es el mismo producto — ver
    'grupo_id'). Si el SKU no fue cruzado todavía, cae al genérico
    marca + tipo de hoja + formato, que puede juntar sub-líneas distintas de
    una misma marca con igual formato (ej. "Elite Ultra" y "Elite Classic"
    4x40) bajo el mismo nombre."""
    if pd.notna(r.get("nombre_estandar")):
        return r["nombre_estandar"]
    if r.get("categoria") == "Servilletas" and pd.notna(r.get("unidades")):
        return f"{r['marca']} Servilletas {r['subcategoria']} {int(r['unidades'])}un"
    if pd.notna(r.get("rollos")) and pd.notna(r.get("metros_rollo")):
        return f"{r['marca']} {r['subcategoria']} {r['metros_rollo']:g}m x{int(r['rollos'])}un"
    return f"{r['marca']} {r['producto']}"


def _tabla_categoria(df_grupo, ocultar_columnas=None, mostrar_formato=False, resaltar_ovella=False):
    """Arma la tabla de una categoría con la fila más barata resaltada en
    verde y las sin dato reciente en rojo translúcido — mismos colores de
    status de siempre, nunca los de marca, para no mezclar identidad con
    semántica de datos. Incluye la URL cruda del producto en una columna
    aparte para que column_config la muestre como link clickeable.
    `ocultar_columnas` permite no repetir Retailer/Marca cuando ya están
    fijos por el contexto (ej. dentro de la pestaña de ese supermercado).
    Alvi es mayorista y muestra 3 precios (lista, socio 1 unidad, socio 2+
    unidades) en vez del par lista/oferta de todos los demás — cuando la
    tabla es 100% Alvi se arman esas 3 columnas en su lugar."""
    # $/Metro se guarda ya formateado como texto ("N/D" incluido) en vez de
    # dejar que un Styler.format() lo resuelva: st.dataframe muestra "None"
    # crudo para celdas nulas de un Styler sin importar el formatter that se
    # le pase, así que el string final tiene que nacer en la celda misma. El
    # valor numérico crudo se guarda aparte (misma posición que las filas)
    # para el resaltado de "más barato", que sí necesita comparar números.
    es_alvi = not df_grupo.empty and (df_grupo["retailer"] == "alvi").all()
    # Otros mayoristas (ej. Liquimax) traen un precio "desde 2 unidades" sin
    # las 3 columnas de Alvi — se muestra como una columna extra al lado.
    hay_precio2 = (
        not es_alvi and "precio_socio2" in df_grupo.columns
        and df_grupo["precio_socio2"].notna().any()
    )
    # Servilletas se comparan en $/unidad; el resto en $/metro. Si la tabla
    # mezcla (no debería, las vistas son por categoría) gana $/metro.
    unidad_ref = "u" if (not df_grupo.empty and (df_grupo["categoria"] == "Servilletas").all()) else "m"
    col_ref = f"$/{unidad_ref}"
    # ¿Hay algún formato mayorista (manga/caja) en este grupo? Si sí, se agregan
    # las columnas "Precio manga" y "Precio pack" para no confundir el precio
    # del envase colectivo con el del pack suelto.
    hay_bulto_grupo = (
        "packs_por_bulto" in df_grupo.columns
        and pd.to_numeric(df_grupo["packs_por_bulto"], errors="coerce").fillna(1).gt(1).any()
    )
    filas = []
    precios_metro = []
    for _, r in df_grupo.iterrows():
        fila = {"Código": r["sku_interno"], "Grupo": _fmt_grupo(r), "Retailer": r["retailer_nombre"], "Marca": r["marca"]}
        if mostrar_formato:
            fila["Formato"] = _fmt_formato(r)
        precio_metro = r["precio_ref"] if pd.notna(r.get("precio_ref")) else None
        # ✏️ = URL reemplazado desde la planilla · 🆕 = SKU nuevo cargado en la
        # planilla, todavía no pasado a productos.csv (provisorio)
        nombre = str(r["producto"])
        if r.get("url_corregido"):
            nombre += " ✏️"
        if r.get("origen_planilla"):
            nombre += " 🆕"
        fila["Producto"] = nombre
        if es_alvi:
            fila["Precio Lista"] = _formatear_clp(r["precio_normal"])
            fila["Socio 1 un"] = _formatear_clp(r["precio"])
            fila["Socio 2 un"] = _formatear_clp(r.get("precio_socio2"))
        else:
            fila["Precio Lista"] = _formatear_clp(r["precio_normal"])
            fila["Precio Oferta"] = _formatear_clp(r["precio"]) if pd.notna(r["descuento_pct"]) else "—"
            fila["Desc."] = f"-{int(r['descuento_pct'])}%" if pd.notna(r["descuento_pct"]) else "—"
            if hay_precio2:
                fila["Desde 2 un"] = _formatear_clp(r.get("precio_socio2")) if pd.notna(r.get("precio_socio2")) else "—"
        if hay_bulto_grupo:
            lbl_bulto = "Precio caja" if unidad_ref == "u" else "Precio manga"
            n = pd.to_numeric(r.get("packs_por_bulto"), errors="coerce")
            es_bulto = pd.notna(n) and n > 1
            fila[lbl_bulto] = _formatear_clp(r.get("precio_manga")) if es_bulto else "—"
            fila["Precio pack"] = _formatear_clp(r.get("precio_pack")) if es_bulto else "—"
        fila[col_ref] = f"${precio_metro}/{unidad_ref}" if precio_metro is not None else "N/D"
        if hay_precio2:
            v2 = r.get("precio_metro_2un")
            fila[f"{col_ref} (2+un)"] = f"${v2}/{unidad_ref}" if pd.notna(v2) else "—"
        fila["Estado"] = r["estado"]
        fila["Ver"] = r.get("url")
        filas.append(fila)
        precios_metro.append(precio_metro)

    tabla = pd.DataFrame(filas)
    if ocultar_columnas:
        tabla = tabla.drop(columns=[c for c in ocultar_columnas if c in tabla.columns])
    validos = [v for v in precios_metro if v is not None]
    minimo = min(validos) if validos else None

    marcas = list(df_grupo["marca"]) if resaltar_ovella else []

    def resaltar(fila):
        if minimo is not None and precios_metro[fila.name] == minimo:
            return [f"background-color: {COLOR_BUENO}26"] * len(fila)
        if fila["Estado"] != "Disponible":
            return [f"background-color: {COLOR_CRITICO}1a"] * len(fila)
        if resaltar_ovella and str(marcas[fila.name]).strip().lower() == "ovella":
            return [f"background-color: {COLOR_MORADO}1f"] * len(fila)
        return [""] * len(fila)

    return tabla.style.apply(resaltar, axis=1)


COLUMN_CONFIG = {
    "Ver": st.column_config.LinkColumn("Ver", display_text="Ver ↗", width="small"),
}


def _mostrar_tabla(df_grupo, **kwargs):
    try:
        st.dataframe(_tabla_categoria(df_grupo, **kwargs), width="stretch", hide_index=True, column_config=COLUMN_CONFIG)
    except TypeError:
        st.dataframe(_tabla_categoria(df_grupo, **kwargs), use_container_width=True, hide_index=True, column_config=COLUMN_CONFIG)


# --- Interfaz ---
try:
    df, ovella_df = cargar_datos()
except Exception as e:
    st.error(f"Aún no hay datos procesados o hubo un error al cargar. Ejecuta el Scraper en Actions. Error: {e}")
    st.stop()

ultima_fecha = None
if "fecha_act" in df.columns and df["fecha_act"].notna().any():
    # "27/08/2026..." le gana a "01/09/2026..." como texto (el '2' de "27"
    # pesa más que el '0' de "01"), así que hay que parsear la fecha antes
    # de comparar — si no, un SKU con dato viejo (ej. Knasta trabado) puede
    # aparecer como "más reciente" que uno recién actualizado.
    fechas = pd.to_datetime(df["fecha_act"], format="%d/%m/%Y %H:%M hrs", errors="coerce")
    if fechas.notna().any():
        ultima_fecha = fechas.max().strftime("%d/%m/%Y %H:%M hrs")

# --- Barra lateral: navegación + filtros + mantenimiento ------------------
with st.sidebar:
    st.markdown(
        f'<img src="data:image/png;base64,{_logo_base64()}" style="height: 30px; margin-bottom: 12px;">',
        unsafe_allow_html=True,
    )
    vista = st.radio(
        "Vista",
        ["🏪 Por retailer", "🥊 Por segmento competitivo", "💼 Para vender"],
    )
    st.markdown("---")
    canal_sel = st.radio(
        "Canal",
        ["Retail", "Mayorista", "Todos"],
        help=(
            "Retail = supermercados (Jumbo, Santa Isabel, Tottus, Unimarc, aCuenta). "
            "Mayorista = distribuidores que venden por manga/caja (Central Mayorista, "
            "Imanweb, Dimak, Alvi, Liquimax). Se separan porque su $/metro no es "
            "comparable de igual a igual con el del retail."
        ),
    )
    busqueda = st.text_input(
        "Buscar", placeholder="Ej. Elite, Confort, doble hoja...",
    )

    # Grupo = el cruce manual "mismo producto entre retailers" (ver grupo_id /
    # nombre_estandar en productos.csv). El selector deja "ver el grupo 9" sin
    # tener que acordarse el número: elegís de la lista y salta a una sola
    # tabla con todos los retailers de ese grupo, precio incluido.
    _grupos_df = df[df["grupo_id"].notna()][["grupo_id", "nombre_estandar"]].drop_duplicates()
    _grupos_df["_num"] = pd.to_numeric(_grupos_df["grupo_id"], errors="coerce")
    _grupos_df = _grupos_df.sort_values(["_num", "grupo_id"])
    opciones_grupo = ["— Todos —"] + [
        f"{r.grupo_id} — {r.nombre_estandar}" for r in _grupos_df.itertuples()
    ]
    grupo_sel = st.selectbox("🔢 Ver un grupo (cruce entre retailers)", opciones_grupo)
    st.markdown("---")

# El CSV descargable SIEMPRE es la base completa (todos los canales, sin
# búsqueda) — se arma antes de aplicar cualquier filtro de pantalla.
df_completo = df.copy()

grupo_id_sel = grupo_sel.split(" — ", 1)[0] if grupo_sel != "— Todos —" else None

# Filtro de canal — se aplica a todo (métricas incluidas) para que los
# números de arriba cuadren con lo que se ve en la tabla.
if canal_sel == "Retail":
    df = df[df["canal"] == "retail"]
elif canal_sel == "Mayorista":
    df = df[df["canal"] == "mayorista"]

if busqueda:
    coincide = (
            df["marca"].str.contains(busqueda, case=False, na=False)
            | df["producto"].str.contains(busqueda, case=False, na=False)
        )
    df = df[coincide]

chip_fecha = f"""
<div style="background: rgba(255,255,255,0.15); border: 1px solid rgba(255,255,255,0.35);
            border-radius: 999px; padding: 8px 18px; color: #FFFFFF; font-size: 0.9rem;
            white-space: nowrap;">
    🕒 Último reporte: <strong>{ultima_fecha or "sin datos aún"}</strong>
</div>
""" if ultima_fecha else ""

st.markdown(f"""
<div style="background: linear-gradient(135deg, {COLOR_MORADO} 0%, {COLOR_MORADO_OSCURO} 100%);
            border-bottom: 5px solid {COLOR_VERDE};
            padding: 22px 32px; border-radius: 12px; margin-bottom: 28px;
            display: flex; align-items: center; justify-content: space-between; gap: 22px; flex-wrap: wrap;">
    <div style="display: flex; align-items: center; gap: 22px;">
        <div style="background: #FFFFFF; border-radius: 10px; padding: 8px 14px; display: flex; align-items: center; box-shadow: 0 1px 4px rgba(0,0,0,0.15);">
            <img src="data:image/png;base64,{_logo_base64()}" style="height: 38px; display: block;">
        </div>
        <div>
            <div style="color: #FFFFFF; font-size: 1.7rem; font-weight: 700; line-height: 1.2;">
                Monitor Competitivo de Precios
            </div>
            <div style="color: #E4D7F7; font-size: 0.95rem; margin-top: 2px;">
                Inteligencia de mercado: pricing de Ovella vs. la competencia en retail
            </div>
        </div>
    </div>
    {chip_fecha}
</div>
""", unsafe_allow_html=True)

if df.empty:
    st.warning("No hay productos que coincidan con el filtro actual (canal / búsqueda).")
    st.stop()

etiqueta_canal = {"Retail": "supermercados", "Mayorista": "mayoristas", "Todos": "retail + mayoristas"}[canal_sel]
# La vista "Para vender" es para el celular del vendedor: se salta las
# métricas de gestión y los paneles de mantenimiento, va directo al comparador.
_ES_VENTA = vista == "💼 Para vender"

if not _ES_VENTA:
    con_descuento = df[df["descuento_pct"].notna()]
    ofertas_agresivas = df[df["descuento_pct"] >= 20]
    pendientes = df[df["estado"] != "Disponible"]

    c1, c2, c3 = st.columns(3)
    c1.metric("SKU monitoreados", len(df), etiqueta_canal, delta_color="off")
    c2.metric("En oferta", len(con_descuento), f"{len(ofertas_agresivas)} con descuento ≥20%", delta_color="off")
    c3.metric("Sin dato reciente", len(pendientes))

    st.divider()

# separador ";" porque el Excel en español/Chile usa "," como separador decimal
# y por lo tanto ";" entre columnas — con "," todo el CSV aparece amontonado en
# una sola columna al abrirlo.
st.sidebar.download_button(
    "⬇️ Descargar CSV (todo)",
    data=_armar_export(df_completo[~df_completo["retailer_desactivado"]]).to_csv(index=False, sep=";").encode("utf-8-sig"),
    file_name="precios_dipisa.csv",
    mime="text/csv",
    width="stretch",
    help="Exporta el monitor completo (todos los retailers y categorías), sin importar el filtro de pantalla.",
)

_buf_jefe = io.BytesIO()
_df_jefe = _armar_export_formato_jefe(df_completo[~df_completo["retailer_desactivado"]])
with pd.ExcelWriter(_buf_jefe, engine="openpyxl") as _xw:
    _df_jefe.to_excel(_xw, index=False, sheet_name="Formato V3.0")
    if not _df_jefe.empty:
        _aplicar_formato_jefe(_xw.sheets["Formato V3.0"], _df_jefe)
st.sidebar.download_button(
    "⬇️ Descargar Excel (formato jefe)",
    data=_buf_jefe.getvalue(),
    file_name="precios_dipisa_formato_jefe.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    width="stretch",
    help="Mismas columnas que la planilla 'Formato Toma Precios' del jefe (Codigo/Fecha/Local/Cat./Fabrica/Sector/Cod/PVP/etc.), pero ya con los precios scrapeados.",
)

with st.sidebar.expander("🔗 ¿Un link de producto está roto o cambió?"):
    if not OVERRIDES_CSV_URL:
        st.info(
            "Función en preparación: falta crear la pestaña **Arreglar Link** "
            "en la planilla y pegar su link publicado en `app.py` / `scraper.py` "
            "(`OVERRIDES_CSV_URL`). Mientras tanto, el link de un producto se "
            "puede corregir editando su fila en la pestaña **Catálogo**. "
            "Ver `TRASPASO.md`."
        )
    else:
        st.markdown(
            "Cuando un supermercado cambia la dirección de un producto, su precio "
            "deja de actualizarse (aparece como *“⚠️ Últ. precio”*). Para arreglarlo "
            "**no hace falta tocar código**: se corrige en la pestaña **Arreglar Link** "
            "de la planilla de Google.\n\n"
            "1. Abrí la planilla.\n"
            "2. Agregá una fila con **Código** (el código del producto, ej. `TC-034`), "
            "**Link nuevo** y una **Nota** opcional.\n"
            "3. En la próxima actualización automática (máx. ~8 h) el precio vuelve solo.\n\n"
            "Los productos con link ya corregido se muestran con un ✏️ al lado del nombre."
        )
    if PLANILLA_EDIT_URL:
        st.link_button("✏️ Abrir la planilla de correcciones", PLANILLA_EDIT_URL)
    else:
        st.caption(
            "⚠️ Falta configurar el link de la planilla en `app.py` "
            "(`PLANILLA_EDIT_URL`) — ver `TRASPASO.md`."
        )

with st.sidebar.expander("➕ Agregar, editar o sacar un producto"):
    if not CATALOGO_CSV_URL:
        st.info(
            "Función en preparación: falta publicar la pestaña **Catálogo** "
            "de la planilla y pegar su link en `app.py` / `scraper.py` "
            "(`CATALOGO_CSV_URL`). Ver `TRASPASO.md`."
        )
    else:
        st.markdown(
            "Todo se hace **sin tocar código**, en la pestaña **Catálogo** de la "
            "planilla: ahí está el catálogo completo, una fila por producto, "
            "**sin** columna Acción.\n\n"
            "- **Agregar** un producto: sumá una fila abajo de todo.\n"
            "- **Editar** un producto: cambiá el valor que haga falta en su fila.\n"
            "- **Sacar** un producto: borrá su fila entera.\n\n"
            "En la próxima corrida automática (máx. ~8 h) lo que quede en la "
            "planilla reemplaza el catálogo del monitor — nadie necesita entrar "
            "a GitHub. Las altas aparecen mientras tanto marcadas con 🆕 "
            "(provisorio); ediciones y bajas se ven recién cuando corre el scraper."
        )
    if PLANILLA_EDIT_URL:
        st.link_button("📋 Abrir la planilla", PLANILLA_EDIT_URL)

    if CATALOGO_CSV_URL:
        catalogo_raw = cargar_catalogo_planilla()
        if not catalogo_raw.empty:
            retailers_ok = set(yaml.safe_load(open(BASE_DIR / "retailers.yaml", encoding="utf-8")).keys())
            problemas = _validar_catalogo_planilla(catalogo_raw, retailers_ok)
            base_actual = set(pd.read_csv(BASE_DIR / "productos.csv", comment="#", skip_blank_lines=True).dropna(subset=["sku_interno"])["sku_interno"])
            caida = len(base_actual) - catalogo_raw["sku_interno"].nunique()
            if base_actual and caida > len(base_actual) * 0.3:
                st.error(
                    f"🚫 La planilla tiene muchos menos productos que el catálogo actual "
                    f"({catalogo_raw['sku_interno'].nunique()} vs {len(base_actual)} — más de 30% menos). "
                    "Si esto no fue intencional, revisen la planilla: el scraper va a "
                    "**ignorar todo el cambio** y seguir con el catálogo actual hasta que se corrija."
                )
            if problemas:
                st.warning("Filas de la planilla con algo para revisar (se ignoran, no frenan al resto):\n\n- " + "\n- ".join(problemas))
            elif not (base_actual and caida > len(base_actual) * 0.3):
                st.caption(f"✅ {catalogo_raw['sku_interno'].nunique()} producto(s) en la planilla, todos válidos.")

estado_scraper = cargar_estado_scraper() if not _ES_VENTA else None
if estado_scraper:
    fallos = estado_scraper.get("fallos", [])
    total = estado_scraper.get("total_skus", 0)
    exitosos = estado_scraper.get("exitosos", 0)
    retailers_off = estado_scraper.get("retailers_desactivados") or []
    sku_off = estado_scraper.get("sku_desactivados", 0)
    icono = "✅" if not fallos else "⚠️"
    with st.expander(f"{icono} Salud del scraper — última corrida: {exitosos}/{total} SKU actualizados ({estado_scraper.get('fecha', '')})"):
        catalogo_sync = estado_scraper.get("catalogo_planilla") or {}
        if catalogo_sync.get("motivo_rechazo"):
            st.error(f"🚫 La planilla de Catálogo se ignoró en la última corrida: {catalogo_sync['motivo_rechazo']}")
        elif catalogo_sync.get("aplicado"):
            st.success(f"📋 Catálogo sincronizado desde la planilla en la última corrida: {catalogo_sync.get('filas_aplicadas', 0)} producto(s).")
        if catalogo_sync.get("problemas"):
            with st.popover("Ver filas de la planilla ignoradas en la última corrida"):
                st.markdown("- " + "\n- ".join(catalogo_sync["problemas"]))

historial_catalogo = cargar_historial_catalogo() if not _ES_VENTA else []
if historial_catalogo:
    ICONO_CAMBIO = {"alta": "🟢 Alta", "baja": "🔴 Baja", "edicion": "✏️ Edición"}
    with st.sidebar.expander(f"🕓 Cambios de catálogo (últimos 30 días, {len(historial_catalogo)})"):
        for c in historial_catalogo:
            st.caption(f"{c['fecha']} · {ICONO_CAMBIO.get(c['tipo'], c['tipo'])} · **{c['sku']}** · {c['detalle']}")
        if estado_scraper.get("catalogo_planilla_aviso"):
            st.caption(f"⚠️ {estado_scraper['catalogo_planilla_aviso']}")
        if retailers_off:
            st.info(
                f"⏸️ **{', '.join(retailers_off)}** deshabilitado ({sku_off} SKU) — bloqueo total "
                "confirmado, no vale la pena reintentar cada corrida. Esos productos muestran su "
                "último precio conocido. Reactivar en `retailers.yaml` si cambia la situación."
            )
        if not fallos:
            st.success("Todos los SKU se actualizaron correctamente en la última corrida.")
        else:
            with open(BASE_DIR / "retailers.yaml", encoding="utf-8") as f:
                retailers_cfg_local = yaml.safe_load(f)
            fallos_df = pd.DataFrame(fallos)
            fallos_df["retailer_nombre"] = fallos_df["retailer"].map(
                lambda r: retailers_cfg_local.get(r, {}).get("nombre", r)
            )
            por_retailer = fallos_df.groupby("retailer_nombre").size().sort_values(ascending=False)
            st.caption("Fallos por retailer en la última corrida (el sitio se cae con un fallback al último precio conocido, no rompe la página):")
            st.dataframe(
                por_retailer.rename("Fallos").reset_index().rename(columns={"retailer_nombre": "Retailer"}),
                hide_index=True, width="stretch",
            )
            with st.popover("Ver detalle de cada fallo"):
                detalle = fallos_df.merge(
                    df[["sku_interno", "producto", "marca"]].drop_duplicates("sku_interno"),
                    on="sku_interno", how="left",
                )
                st.dataframe(
                    detalle[["retailer_nombre", "sku_interno", "marca", "producto", "error"]]
                    .rename(columns={"retailer_nombre": "Retailer", "sku_interno": "SKU", "marca": "Marca", "producto": "Producto", "error": "Error"}),
                    hide_index=True, width="stretch",
                )

historial = cargar_historial() if not _ES_VENTA else pd.DataFrame()
semanas = sorted(historial["semana"].unique()) if not historial.empty else []
with (contextlib.nullcontext() if _ES_VENTA else st.expander("📈 Cambios de precio esta semana")):
  if not _ES_VENTA:
    if len(semanas) < 2:
        st.info("Todavía no hay dos semanas de historial para comparar — el sistema recién empezó a guardar precios semana a semana. Vuelve a revisar más adelante.")
    else:
        semana_actual, semana_anterior = semanas[-1], semanas[-2]
        pivote = historial[historial["semana"].isin([semana_actual, semana_anterior])].pivot_table(
            index="sku_interno", columns="semana", values="precio", aggfunc="last"
        )
        pivote = pivote.dropna(subset=[semana_actual, semana_anterior])
        pivote["variacion_pct"] = ((pivote[semana_actual] - pivote[semana_anterior]) / pivote[semana_anterior] * 100).round(1)
        pivote = pivote[pivote["variacion_pct"] != 0].reset_index()
        pivote = pivote.merge(df[["sku_interno", "marca", "producto", "retailer_nombre"]].drop_duplicates("sku_interno"), on="sku_interno", how="inner")

        if pivote.empty:
            st.info("Ningún precio cambió entre la semana pasada y esta.")
        else:
            def _tabla_movimientos(sub_df):
                vista = sub_df[["retailer_nombre", "marca", "producto", semana_anterior, semana_actual, "variacion_pct"]].copy()
                vista.columns = ["Retailer", "Marca", "Producto", "Precio anterior", "Precio actual", "Variación %"]
                vista["Precio anterior"] = vista["Precio anterior"].map(_formatear_clp)
                vista["Precio actual"] = vista["Precio actual"].map(_formatear_clp)
                vista["Variación %"] = vista["Variación %"].map(lambda v: f"{'+' if v > 0 else ''}{v:g}%")
                st.dataframe(vista, hide_index=True, width="stretch")

            subieron = pivote[pivote["variacion_pct"] > 0].sort_values("variacion_pct", ascending=False).head(10)
            bajaron = pivote[pivote["variacion_pct"] < 0].sort_values("variacion_pct").head(10)
            col_sube, col_baja = st.columns(2)
            with col_sube:
                st.markdown("**⬆️ Subieron más**")
                if not subieron.empty:
                    _tabla_movimientos(subieron)
                else:
                    st.caption("Ninguno.")
            with col_baja:
                st.markdown("**⬇️ Bajaron más**")
                if not bajaron.empty:
                    _tabla_movimientos(bajaron)
                else:
                    st.caption("Ninguno.")

st.divider()

ICONO_CATEGORIA = {"Papel Higienico": "🧻", "Toalla de Papel": "🧺", "Servilletas": "🍽️"}


def _mostrar_marcas(df_sub):
    for marca in sorted(df_sub["marca"].dropna().unique()):
        grupo_marca = df_sub[df_sub["marca"] == marca]
        st.markdown(f"##### {marca} ({len(grupo_marca)})")
        _mostrar_tabla(grupo_marca, ocultar_columnas=["Retailer", "Marca"], mostrar_formato=True)


def _detalle_pack(r):
    """'pack 4×22 m · $3.190' — el envase de góndola con su precio, para que
    el vendedor sepa exactamente qué producto es el que se está comparando."""
    p_pack = r.get("precio_pack")
    if pd.isna(p_pack):
        p_pack = r.get("precio")
    if pd.notna(r.get("rollos")) and pd.notna(r.get("metros_rollo")):
        base = f"pack {int(r['rollos'])}×{r['metros_rollo']:g} m"
    elif pd.notna(r.get("unidades")):
        base = f"pack {int(r['unidades'])} un"
    else:
        base = "pack"
    return f"{base} · {_formatear_clp(p_pack)}" if pd.notna(p_pack) else base


def _para_vender(dfx, canal_sel):
    """Vista para vendedores: elige un formato y muestra el precio de Ovella
    contra los competidores de ese mismo formato, con un veredicto en lenguaje
    simple y el rango del mercado. Cada precio muestra de qué SKU y retailer
    sale — no son promedios, es el SKU más barato de cada marca en ese
    formato y canal."""
    st.markdown("### 💼 Comparador para vender")
    if canal_sel == "Todos":
        st.warning(
            "Estás viendo **retail + mayorista juntos**. El $/metro de un mayorista "
            "(precio de manga bajado a pack) no compite de igual a igual con el de "
            "un supermercado. Para vender, elegí **Retail** o **Mayorista** en la "
            "barra lateral."
        )
    else:
        st.info(f"Canal: **{'🏪 Retail (supermercados)' if canal_sel == 'Retail' else '🏭 Mayorista (distribuidores)'}** — cambialo en la barra lateral.")

    base = dfx[dfx["segmento"].notna() & dfx["precio_ref"].notna()].copy()
    base["_marca"] = base["marca"].str.strip()
    base["_es_ov"] = base["_marca"].str.lower() == "ovella"
    segs = sorted(base.loc[base["_es_ov"], "segmento"].unique())
    if not segs:
        st.info(
            "En este canal todavía no hay ningún producto **Ovella** con precio "
            "para comparar. Ovella hoy tiene más precios cargados en el canal "
            "**mayorista** (Alvi, Central Mayorista, Imanweb)."
        )
        return

    def _fmt_seg(s):
        return s.replace("·", "—")
    seg = st.selectbox("Elegí el formato", segs, format_func=_fmt_seg)

    g = base[base["segmento"] == seg].copy()
    unidad = g["ref_unidad"].iloc[0]
    nu = "metro" if unidad == "m" else "unidad"

    ov = g[g["_es_ov"]].sort_values("precio_ref")
    ov_precio = float(ov["precio_ref"].min())

    # --- Ovella: todos sus SKU en este formato -------------------------------
    st.markdown(f"**Ovella en {_fmt_seg(seg)}** ($/{nu}):")
    st.dataframe(
        pd.DataFrame([{
            "Producto": r["producto"],
            "Retailer": r["retailer_nombre"],
            "Pack": _detalle_pack(r),
            f"$/{nu}": f"${r['precio_ref']:g}",
        } for _, r in ov.iterrows()]),
        hide_index=True, width="stretch",
    )
    if len(ov) > 1:
        st.caption(f"Para el veredicto se usa el más barato: **${ov_precio:g}/{nu}**.")

    # --- Competencia: SKU más barato de cada marca -------------------------
    comp_raw = g[~g["_es_ov"]]
    if comp_raw.empty:
        st.info("No hay competidores con precio en este formato todavía.")
        comp = pd.DataFrame(columns=["_marca", "precio_ref"])
    else:
        idx = comp_raw.groupby("_marca")["precio_ref"].idxmin()
        comp = comp_raw.loc[idx].sort_values("precio_ref")
        st.markdown(f"**Competencia** — el SKU más barato de cada marca ($/{nu}):")
        st.dataframe(
            pd.DataFrame([{
                "Marca": r["_marca"],
                "Producto": r["producto"],
                "Retailer": r["retailer_nombre"],
                "Pack": _detalle_pack(r),
                f"$/{nu}": f"${r['precio_ref']:g}",
                "vs Ovella": f"{(r['precio_ref'] - ov_precio) / ov_precio * 100:+.0f}%",
            } for _, r in comp.iterrows()]),
            hide_index=True, width="stretch",
        )

    # --- Veredicto --------------------------------------------------------
    if not comp.empty:
        mb = comp.iloc[0]
        if ov_precio <= mb["precio_ref"]:
            d = (mb["precio_ref"] - ov_precio) / ov_precio * 100
            st.success(
                f"✅ **Ovella es la más barata de este formato.** "
                f"La más cercana es {mb['_marca']} a ${mb['precio_ref']:g}/{nu} (+{d:.0f}%)."
            )
        else:
            d = (ov_precio - mb["precio_ref"]) / mb["precio_ref"] * 100
            debajo = comp[comp["precio_ref"] > ov_precio]["_marca"].tolist()
            extra = f" Igual le gana a {', '.join(debajo)}." if debajo else ""
            st.warning(
                f"⚠️ **Ovella está +{d:.0f}% sobre {mb['_marca']}** "
                f"(${ov_precio:g} vs ${mb['precio_ref']:g}/{nu}).{extra}"
            )

    lo, hi = g["precio_ref"].min(), g["precio_ref"].max()
    med = g["precio_ref"].median()
    st.caption(
        f"📊 Mercado de este formato: **${lo:g} – ${hi:g}/{nu}** (mediana ${med:g}). "
        f"{g['_marca'].nunique()} marcas · {g['retailer_nombre'].nunique()} retailers.  \n"
        f"ℹ️ Cada precio es el **SKU más barato** de esa marca en este formato y canal "
        f"(no es un promedio). El formato lo define el pack: {_fmt_seg(seg)}."
    )


if grupo_id_sel:
    # Un grupo elegido en la barra lateral manda por sobre la Vista: una sola
    # tabla con todos los retailers de ese grupo, precio incluido.
    g_nombre = grupo_sel.split(" — ", 1)[1] if " — " in grupo_sel else ""
    df_g = df[df["grupo_id"].astype(str) == str(grupo_id_sel)].sort_values("precio_ref", na_position="last")
    st.markdown(f"### 🔢 Grupo {grupo_id_sel} — {g_nombre}")
    if df_g.empty:
        st.info("Este grupo no tiene productos en el canal/búsqueda actual. Probá cambiar el Canal en la barra lateral.")
    else:
        st.caption(f"{len(df_g)} productos · {df_g['retailer_nombre'].nunique()} retailers · canal {etiqueta_canal}.")
        _mostrar_tabla(df_g, mostrar_formato=True, resaltar_ovella=True)

elif vista == "💼 Para vender":
    _para_vender(df, canal_sel)

elif vista == "🏪 Por retailer":
    # Un tab por supermercado, y dentro de cada uno las filas agrupadas por marca.
    retailers_activos = sorted(df["retailer_nombre"].dropna().unique())
    tabs_retailer = st.tabs(retailers_activos)

    for tab, retailer_nombre in zip(tabs_retailer, retailers_activos):
        with tab:
            df_retailer = df[df["retailer_nombre"] == retailer_nombre]
            st.caption(f"{len(df_retailer)} productos monitoreados en {retailer_nombre}")

            # Solo se separa por categoría (Higiénico/Toalla/Servilletas) si ese
            # retailer tiene más de una — si no, el sub-selector no aportaría nada.
            categorias_retailer = sorted(df_retailer["categoria"].dropna().unique())
            if len(categorias_retailer) <= 1:
                _mostrar_marcas(df_retailer)
            else:
                tabs_categoria = st.tabs([f"{ICONO_CATEGORIA.get(c, '📄')} {c}" for c in categorias_retailer])
                for tab_cat, categoria in zip(tabs_categoria, categorias_retailer):
                    with tab_cat:
                        _mostrar_marcas(df_retailer[df_retailer["categoria"] == categoria])

else:
    # Un segmento = tipo de hoja + tamaño de pack + metros por rollo. Filtrar
    # por uno muestra todos los competidores directos de ese formato, de todos
    # los supermercados, ordenados del $/metro más barato al más caro. Las
    # filas de Ovella van resaltadas en morado.
    st.caption(
        "Cada segmento junta productos que compiten de verdad: mismo tipo de hoja, "
        "pack parecido (a más rollos, mejor $/metro) y metraje por rollo similar. "
        f"Mostrando **{etiqueta_canal}** (cambiar en la barra lateral › Canal)."
    )
    df_seg = df[df["segmento"].notna()]
    cats = sorted(df_seg["categoria"].dropna().unique())
    cat_sel = st.radio("Categoría", cats, horizontal=True, key="seg_cat")
    df_seg = df_seg[df_seg["categoria"] == cat_sel]

    conteo = df_seg.groupby("segmento").size()
    segs_ovella = set(ovella_df.loc[ovella_df["categoria"] == cat_sel, "segmento"].dropna())
    # Los segmentos donde Ovella tiene un producto van primero y marcados.
    opciones = sorted(conteo.index, key=lambda s: (s not in segs_ovella, s))
    etiqueta = {
        s: f"{'⭐ ' if s in segs_ovella else ''}{s}  ({conteo[s]} SKU)"
        for s in opciones
    }
    seg_sel = st.selectbox(
        "Segmento", opciones, format_func=lambda s: etiqueta[s], key="seg_sel"
    )

    df_match = df_seg[df_seg["segmento"] == seg_sel].sort_values(
        "precio_ref", na_position="last"
    )
    n_marcas = df_match["marca"].nunique()
    n_retailers = df_match["retailer_nombre"].nunique()
    st.markdown(
        f"**{len(df_match)} productos** · {n_marcas} marcas · {n_retailers} retailers"
        + ("  ·  ⭐ Ovella compite en este segmento" if seg_sel in segs_ovella else "")
    )
    _mostrar_tabla(df_match, mostrar_formato=True, resaltar_ovella=True)

st.caption("Se actualiza automáticamente 3 veces al día vía GitHub Actions.")
