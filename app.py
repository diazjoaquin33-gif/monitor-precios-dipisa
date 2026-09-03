import streamlit as st
import pandas as pd
import yaml
import json
import io
import base64
import requests
from pathlib import Path

st.set_page_config(
    page_title="Monitor de Precios | Dipisa",
    page_icon="🧻",
    layout="wide",
    initial_sidebar_state="collapsed",
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
header {{visibility: hidden;}}

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
OVERRIDES_CACHE_PATH = BASE_DIR / "url_overrides_cache.json"
PRODUCTOS_NUEVOS_CACHE_PATH = BASE_DIR / "productos_nuevos_cache.csv"

# Planilla de Google publicada (cuenta monitor.de.precios1@gmail.com).
# Pestaña 1 (url_fixes): sku_interno,url_nuevo,nota — reemplazar un URL muerto.
OVERRIDES_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTMZ7qyGdu79TJ5CUPN5dfIf4YZDgV9JqDpDdW8dA_jiqCrYDcW3RO_hGqjRp12QnKWKTvlkKvV1nWX/pub?gid=0&single=true&output=csv"
# Pestaña 2 (productos_nuevos): mismas columnas que productos.csv — sumar un SKU
# nuevo sin tocar código. Vacío = función desactivada.
PRODUCTOS_NUEVOS_CSV_URL = ""
# Link para EDITAR la planilla (barra de direcciones al abrirla, termina en /edit).
# Si queda vacío, la app no muestra los botones que llevan a ella.
PLANILLA_EDIT_URL = "https://docs.google.com/spreadsheets/d/1Ka3EM2FEWd3uyfZ3CgzxeJhwQ9adETOvU0cihdPiBRw/edit"


@st.cache_data(ttl=600)
def cargar_overrides_url():
    """Correcciones de URL cargadas por el equipo en la planilla de Google. Si la
    planilla no responde, cae a la copia local del repo. Nunca rompe la app."""
    try:
        res = requests.get(OVERRIDES_CSV_URL, timeout=15)
        res.raise_for_status()
        df = pd.read_csv(io.StringIO(res.text)).dropna(subset=["sku_interno", "url_nuevo"])
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
    "categoria", "subcategoria", "rollos", "metros_rollo",
]


@st.cache_data(ttl=600)
def cargar_productos_nuevos():
    """SKU que el equipo cargó en la pestaña 'productos_nuevos' de la planilla.
    Si la planilla no responde, cae a la copia local. Nunca rompe la app."""
    if not PRODUCTOS_NUEVOS_CSV_URL:
        return pd.DataFrame(columns=COLUMNAS_PRODUCTOS)
    try:
        res = requests.get(PRODUCTOS_NUEVOS_CSV_URL, timeout=15)
        res.raise_for_status()
        df = pd.read_csv(io.StringIO(res.text))
    except Exception:
        if PRODUCTOS_NUEVOS_CACHE_PATH.exists():
            try:
                df = pd.read_csv(PRODUCTOS_NUEVOS_CACHE_PATH)
            except Exception:
                return pd.DataFrame(columns=COLUMNAS_PRODUCTOS)
        else:
            return pd.DataFrame(columns=COLUMNAS_PRODUCTOS)
    df = df.dropna(subset=["sku_interno", "retailer", "url"])
    for col in COLUMNAS_PRODUCTOS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[COLUMNAS_PRODUCTOS]


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


@st.cache_data(ttl=600)
def cargar_datos():
    with open(BASE_DIR / "datos_procesados.json", "r", encoding="utf-8") as f:
        datos = json.load(f)
    df_json = pd.DataFrame(datos)

    df_csv = pd.read_csv(BASE_DIR / "productos.csv", comment="#", skip_blank_lines=True)
    df_csv = df_csv.dropna(subset=["sku_interno"])
    df_csv["origen_planilla"] = False

    # SKU nuevos cargados por el equipo en la planilla (pestaña productos_nuevos):
    # se muestran como cualquier otro pero marcados "provisorio" hasta que alguien
    # los pase a productos.csv formalmente.
    nuevos = cargar_productos_nuevos()
    nuevos = nuevos[~nuevos["sku_interno"].isin(df_csv["sku_interno"])]
    if not nuevos.empty:
        nuevos = nuevos.copy()
        nuevos["origen_planilla"] = True
        df_csv = pd.concat([df_csv, nuevos], ignore_index=True)

    # URLs corregidos a mano por el equipo desde la planilla de Google — se
    # aplican encima del CSV para que el "Ver ↗" apunte al link vigente.
    overrides = cargar_overrides_url()
    df_csv["url_corregido"] = df_csv["sku_interno"].isin(list(overrides))
    df_csv["url"] = df_csv.apply(
        lambda r: overrides.get(r["sku_interno"], r["url"]), axis=1
    )

    with open(BASE_DIR / "retailers.yaml", "r", encoding="utf-8") as f:
        retailers_cfg = yaml.safe_load(f)

    # LEFT join (no inner): un producto sin precio todavía (recién agregado,
    # o que lleva varias corridas fallando) sigue apareciendo como
    # "Pendiente" en vez de desaparecer en silencio de la página.
    df = pd.merge(df_csv, df_json, on="sku_interno", how="left")
    df["retailer_nombre"] = df["retailer"].map(lambda r: retailers_cfg.get(r, {}).get("nombre", r))

    df["precio"] = pd.to_numeric(df["precio"], errors="coerce")
    df["precio_normal"] = pd.to_numeric(df["precio_normal"], errors="coerce")
    # Solo Alvi trae este campo (precio socio comprando 2+ unidades) — no
    # existe como columna si aún no hay ningún SKU de Alvi con datos.
    df["precio_socio2"] = pd.to_numeric(df["precio_socio2"], errors="coerce") if "precio_socio2" in df.columns else pd.NA
    df["precio_metro"] = (df["precio"] / df["metros_totales"]).round(1)

    descuento = (1 - df["precio"] / df["precio_normal"]) * 100
    df["descuento_pct"] = descuento.round(0)
    df.loc[df["descuento_pct"] <= 0, "descuento_pct"] = pd.NA

    df["estado"] = df["estado"].fillna("Pendiente")

    df["rollos"] = pd.to_numeric(df.get("rollos"), errors="coerce")
    df["metros_rollo"] = pd.to_numeric(df.get("metros_rollo"), errors="coerce")
    df["segmento"] = df.apply(_segmento, axis=1)

    ovella_df = pd.read_csv(BASE_DIR / "ovella.csv", comment="#", skip_blank_lines=True)
    ovella_df = ovella_df.dropna(subset=["sku_ovella"])
    ovella_df["segmento"] = ovella_df.apply(_segmento, axis=1)

    return df, ovella_df


def _formatear_clp(valor):
    if pd.isna(valor):
        return "N/D"
    return f"${valor:,.0f}".replace(",", ".")


# --- Segmento competitivo -------------------------------------------------
# Dos productos compiten de verdad cuando el comprador elige entre ellos en
# la góndola: mismo tipo de hoja, mismo tamaño de pack (a más rollos, mejor
# precio por metro — no se cruza un pack de 4 con uno de 40) y metraje por
# rollo parecido. El segmento junta esos tres ejes. Los cortes de pack son
# casi exactos abajo (1/2/3/4/6) porque ahí está la pelea real; arriba se
# agrupan formatos vecinos (16-18, 20-24). Ajustar acá si hace falta.
def _bucket_pack(rollos):
    r = int(rollos)
    if r <= 1: return "1 un"
    if r <= 3: return f"{r} un"
    if r == 4: return "4 un"
    if r in (5, 6): return "6 un"
    if 7 <= r <= 10: return "8-10 un"
    if r in (11, 12): return "12 un"
    if 13 <= r <= 18: return "16-18 un"
    if 19 <= r <= 30: return "24 un"
    return "40+ un"


def _bucket_metros(metros_rollo):
    m = float(metros_rollo)
    if m <= 17: return "~15 m/rollo"
    if m <= 25: return "~20 m/rollo"
    if m <= 35: return "~30 m/rollo"
    if m <= 48: return "~40 m/rollo"
    if m <= 60: return "~50 m/rollo"
    if m <= 90: return "~70 m/rollo"
    return "100+ m/rollo"


def _segmento(row):
    if pd.isna(row.get("rollos")) or pd.isna(row.get("metros_rollo")):
        return None
    return f"{row['subcategoria']} · {_bucket_pack(row['rollos'])} · {_bucket_metros(row['metros_rollo'])}"


def _armar_export(df_export):
    """Versión "para humanos" del dataframe interno, pensada para abrirse en
    Excel: nombres de columna en español, precios ya formateados en CLP en
    vez de floats crudos, y sin las columnas internas (sku_interno, index de
    merge, etc.) que no significan nada fuera de la app."""
    filas = []
    for _, r in df_export.iterrows():
        filas.append({
            "Retailer": r["retailer_nombre"],
            "Categoría": r["categoria"],
            "Subcategoría": r["subcategoria"],
            "Segmento": r.get("segmento") or "",
            "Producto estándar": _producto_estandar(r),
            "Marca": r["marca"],
            "Producto": r["producto"],
            "Metros totales": r["metros_totales"],
            "Precio Lista": _formatear_clp(r["precio_normal"]),
            "Precio Oferta": _formatear_clp(r["precio"]) if pd.notna(r["descuento_pct"]) else "",
            "Descuento %": f"{int(r['descuento_pct'])}%" if pd.notna(r["descuento_pct"]) else "",
            "Precio Socio 2 un (solo Alvi)": _formatear_clp(r.get("precio_socio2")) if r["retailer"] == "alvi" and pd.notna(r.get("precio_socio2")) else "",
            "$/Metro": f"${r['precio_metro']}/m" if pd.notna(r["precio_metro"]) else "N/D",
            "Estado": r["estado"],
            "Última actualización": r.get("fecha_act") or "",
            "Link": r.get("url"),
        })
    return pd.DataFrame(filas)


def _armar_export_pivote(df_export):
    """CSV en formato "largo" y con precios NUMÉRICOS (no "$1.234"), pensado
    para armar una tabla dinámica en Excel: una fila por producto+retailer,
    'Producto estándar' como campo de fila y 'Retailer' como campo de columna
    para ver de un vistazo cuánto cuesta cada producto en cada supermercado."""
    filas = []
    for _, r in df_export.iterrows():
        filas.append({
            "Producto estándar": _producto_estandar(r),
            "Marca": r["marca"],
            "Categoría": r["categoria"],
            "Subcategoría": r["subcategoria"],
            "Segmento": r.get("segmento") or "",
            "Rollos": int(r["rollos"]) if pd.notna(r.get("rollos")) else None,
            "Metros por rollo": r["metros_rollo"] if pd.notna(r.get("metros_rollo")) else None,
            "Metros totales": r["metros_totales"],
            "Retailer": r["retailer_nombre"],
            "Precio vigente": int(r["precio"]) if pd.notna(r["precio"]) else None,
            "Precio lista": int(r["precio_normal"]) if pd.notna(r["precio_normal"]) else None,
            "Descuento %": int(r["descuento_pct"]) if pd.notna(r["descuento_pct"]) else None,
            "Precio por metro": r["precio_metro"] if pd.notna(r["precio_metro"]) else None,
            "Estado": r["estado"],
            "Actualización": r.get("fecha_act") or "",
            "Link": r.get("url"),
        })
    return pd.DataFrame(filas)


def _fmt_formato(r):
    if pd.notna(r.get("rollos")) and pd.notna(r.get("metros_rollo")):
        return f"{int(r['rollos'])}x{r['metros_rollo']:g}m · {r['subcategoria']}"
    return f"{r['categoria']} · {r['subcategoria']}"


def _producto_estandar(r):
    """Nombre idéntico para el mismo producto en cualquier retailer: marca +
    tipo de hoja + formato. Sirve como campo de fila en una tabla dinámica de
    Excel (filtrás por este nombre y ves el precio en cada supermercado).
    Ojo: agrupa por marca+formato, así que sub-líneas de una misma marca con
    igual formato (ej. "Elite Ultra" y "Elite Soft & Strong" 4x40) caen bajo
    el mismo nombre — si eso pasa se nota como dos precios muy distintos."""
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
    filas = []
    precios_metro = []
    for _, r in df_grupo.iterrows():
        fila = {"Retailer": r["retailer_nombre"], "Marca": r["marca"]}
        if mostrar_formato:
            fila["Formato"] = _fmt_formato(r)
        precio_metro = r["precio_metro"] if pd.notna(r["precio_metro"]) else None
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
        fila["$/Metro"] = f"${precio_metro}/m" if precio_metro is not None else "N/D"
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

con_descuento = df[df["descuento_pct"].notna()]
ofertas_agresivas = df[df["descuento_pct"] >= 20]
pendientes = df[df["estado"] != "Disponible"]

c1, c2, c3, c4 = st.columns(4)
c1.metric("SKU de Ovella", len(ovella_df))
c2.metric("Competidores monitoreados", len(df))
c3.metric("En oferta", len(con_descuento), f"{len(ofertas_agresivas)} con descuento ≥20%", delta_color="off")
c4.metric("Sin dato reciente", len(pendientes))

st.divider()

col_buscar, col_descargar = st.columns([3, 1])
busqueda = col_buscar.text_input(
    "Buscar por marca o producto", placeholder="Ej. Elite, Confort, doble hoja...",
)
if busqueda:
    coincide = (
        df["marca"].str.contains(busqueda, case=False, na=False)
        | df["producto"].str.contains(busqueda, case=False, na=False)
    )
    df = df[coincide]

col_descargar.markdown("<div style='margin-top: 28px'></div>", unsafe_allow_html=True)
# separador ";" y decimal "," porque el Excel en español/Chile usa "," como
# separador decimal y por lo tanto ";" entre columnas — con "," todo el CSV
# aparece amontonado en una sola columna al abrirlo.
col_descargar.download_button(
    "⬇️ CSV para leer",
    data=_armar_export(df).to_csv(index=False, sep=";").encode("utf-8-sig"),
    file_name="precios_dipisa.csv",
    mime="text/csv",
    width="stretch",
)
col_descargar.download_button(
    "📊 CSV para tabla dinámica",
    data=_armar_export_pivote(df).to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
    file_name="precios_dipisa_pivote.csv",
    mime="text/csv",
    width="stretch",
    help="Precios numéricos, una fila por producto+supermercado. En Excel: "
         "Insertar → Tabla dinámica; fila = 'Producto estándar', columna = "
         "'Retailer', valor = 'Precio vigente'.",
)

with st.expander("🔗 ¿Un link de producto está roto o cambió?"):
    st.markdown(
        "Cuando un supermercado cambia la dirección de un producto, su precio "
        "deja de actualizarse (aparece como *“⚠️ Últ. precio”*). Para arreglarlo "
        "**no hace falta tocar código**: se corrige en una planilla de Google.\n\n"
        "1. Abrí la planilla de correcciones.\n"
        "2. Agregá una fila con el **código del producto** (ej. `TC-034`), el "
        "**URL nuevo** y una nota opcional.\n"
        "3. En la próxima actualización automática (máx. ~8 h) el precio vuelve solo.\n\n"
        "Los productos con URL ya corregido se muestran con un ✏️ al lado del nombre."
    )
    if PLANILLA_EDIT_URL:
        st.link_button("✏️ Abrir la planilla de correcciones", PLANILLA_EDIT_URL)
    else:
        st.caption(
            "⚠️ Falta configurar el link de la planilla en `app.py` "
            "(`PLANILLA_EDIT_URL`) — ver `TRASPASO.md`."
        )

with st.expander("➕ Agregar un producto nuevo para monitorear"):
    if not PRODUCTOS_NUEVOS_CSV_URL:
        st.info(
            "Función en preparación: falta publicar la pestaña **productos_nuevos** "
            "de la planilla y pegar su link en `app.py` / `scraper.py` "
            "(`PRODUCTOS_NUEVOS_CSV_URL`). Ver `TRASPASO.md`."
        )
    else:
        st.markdown(
            "Para sumar un producto **sin tocar código**, cargalo en la pestaña "
            "**productos_nuevos** de la planilla, una fila con estas columnas:\n\n"
            "`sku_interno` (código libre, ej. `TC-500`) · `producto` · `marca` · "
            "`metros_totales` · `retailer` (clave exacta: `jumbo`, `santaisabel`, "
            "`tottus`, `unimarc`, `alvi`, `acuenta`) · `url` · `categoria` · "
            "`subcategoria` · `rollos` · `metros_rollo`\n\n"
            "En la próxima corrida (máx. ~8 h) aparece en el dashboard marcado "
            "con 🆕 (provisorio). Cada tanto alguien pasa esas filas a "
            "`productos.csv` y limpia la pestaña."
        )
    if PLANILLA_EDIT_URL:
        st.link_button("📋 Abrir la planilla", PLANILLA_EDIT_URL)

estado_scraper = cargar_estado_scraper()
if estado_scraper:
    fallos = estado_scraper.get("fallos", [])
    total = estado_scraper.get("total_skus", 0)
    exitosos = estado_scraper.get("exitosos", 0)
    icono = "✅" if not fallos else "⚠️"
    with st.expander(f"{icono} Salud del scraper — última corrida: {exitosos}/{total} SKU actualizados ({estado_scraper.get('fecha', '')})"):
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

historial = cargar_historial()
semanas = sorted(historial["semana"].unique()) if not historial.empty else []
with st.expander("📈 Cambios de precio esta semana"):
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


vista = st.radio(
    "Ver por",
    ["🏪 Retailer", "🥊 Segmento competitivo"],
    horizontal=True,
    label_visibility="collapsed",
)

if vista == "🏪 Retailer":
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
        "pack parecido (a más rollos, mejor $/metro) y metraje por rollo similar."
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
        "precio_metro", na_position="last"
    )
    n_marcas = df_match["marca"].nunique()
    n_retailers = df_match["retailer_nombre"].nunique()
    st.markdown(
        f"**{len(df_match)} productos** · {n_marcas} marcas · {n_retailers} supermercados"
        + ("  ·  ⭐ Ovella compite en este segmento" if seg_sel in segs_ovella else "")
    )
    _mostrar_tabla(df_match, mostrar_formato=True, resaltar_ovella=True)

st.caption("Se actualiza automáticamente 3 veces al día vía GitHub Actions.")
