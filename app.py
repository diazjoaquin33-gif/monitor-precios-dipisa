import streamlit as st
import pandas as pd
import yaml
import json
import base64
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

    ovella_df = pd.read_csv(BASE_DIR / "ovella.csv", comment="#", skip_blank_lines=True)
    ovella_df = ovella_df.dropna(subset=["sku_ovella"])

    return df, ovella_df


def _formatear_clp(valor):
    if pd.isna(valor):
        return "N/D"
    return f"${valor:,.0f}".replace(",", ".")


def _tabla_categoria(df_grupo, ocultar_columnas=None, mostrar_formato=False):
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
            fila["Formato"] = f"{r['categoria']} · {r['subcategoria']}"
        precio_metro = r["precio_metro"] if pd.notna(r["precio_metro"]) else None
        fila["Producto"] = r["producto"]
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

    def resaltar(fila):
        if minimo is not None and precios_metro[fila.name] == minimo:
            return [f"background-color: {COLOR_BUENO}26"] * len(fila)
        if fila["Estado"] != "Disponible":
            return [f"background-color: {COLOR_CRITICO}1a"] * len(fila)
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
col_descargar.download_button(
    "⬇️ Descargar CSV",
    data=df.drop(columns=["retailer"], errors="ignore").to_csv(index=False).encode("utf-8-sig"),
    file_name="precios_dipisa.csv",
    mime="text/csv",
    width="stretch",
)

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


# Vista de catálogo: un tab por supermercado, y dentro de cada uno las filas
# agrupadas por marca. La vista "por SKU de Ovella" se sacó por ahora — se
# retoma más adelante.
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

st.caption("Se actualiza automáticamente 3 veces al día vía GitHub Actions.")
