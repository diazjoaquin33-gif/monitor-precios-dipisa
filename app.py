import streamlit as st
import pandas as pd
import yaml
import json
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
COLOR_TEXTO = "#1B252C"
COLOR_BUENO = "#0ca30c"
COLOR_CRITICO = "#d03b3b"

st.markdown(f"""
<style>
#MainMenu {{visibility: hidden;}}
footer {{visibility: hidden;}}
header {{visibility: hidden;}}
h2, h3 {{ color: {COLOR_MORADO}; }}
[data-testid="stMetricValue"] {{ color: {COLOR_TEXTO}; }}
</style>
""", unsafe_allow_html=True)


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


def _tabla_categoria(df_grupo):
    """Arma la tabla de una categoría con la fila más barata resaltada en
    verde y las sin dato reciente en rojo translúcido — mismos colores de
    status de siempre, nunca los de marca, para no mezclar identidad con
    semántica de datos."""
    filas = []
    for _, r in df_grupo.iterrows():
        filas.append({
            "Retailer": r["retailer_nombre"],
            "Marca": r["marca"],
            "Producto": r["producto"],
            "Precio Lista": _formatear_clp(r["precio_normal"]),
            "Precio Oferta": _formatear_clp(r["precio"]) if pd.notna(r["descuento_pct"]) else "—",
            "Desc.": f"-{int(r['descuento_pct'])}%" if pd.notna(r["descuento_pct"]) else "—",
            "$/Metro": r["precio_metro"] if pd.notna(r["precio_metro"]) else None,
            "Estado": r["estado"],
        })
    tabla = pd.DataFrame(filas)
    minimo = tabla["$/Metro"].dropna().min() if tabla["$/Metro"].notna().any() else None

    def resaltar(fila):
        if minimo is not None and fila["$/Metro"] == minimo:
            return [f"background-color: {COLOR_BUENO}26"] * len(fila)
        if fila["Estado"] != "Disponible":
            return [f"background-color: {COLOR_CRITICO}1a"] * len(fila)
        return [""] * len(fila)

    return tabla.style.apply(resaltar, axis=1).format({"$/Metro": lambda v: f"${v}/m" if pd.notna(v) else "N/D"})


def _mostrar_tabla(df_grupo):
    try:
        st.dataframe(_tabla_categoria(df_grupo), width="stretch", hide_index=True)
    except TypeError:
        st.dataframe(_tabla_categoria(df_grupo), use_container_width=True, hide_index=True)


# --- Interfaz ---
try:
    df, ovella_df = cargar_datos()
except Exception as e:
    st.error(f"Aún no hay datos procesados o hubo un error al cargar. Ejecuta el Scraper en Actions. Error: {e}")
    st.stop()

col_logo, col_titulo = st.columns([1, 6])
with col_logo:
    st.image(str(BASE_DIR / "logo.png"), width=90)
with col_titulo:
    st.title("Monitor Competitivo de Precios")
    st.caption("Inteligencia de mercado: pricing de Ovella vs. la competencia en retail")

st.divider()

con_descuento = df[df["descuento_pct"].notna()]
ofertas_agresivas = df[df["descuento_pct"] >= 20]
pendientes = df[df["precio"].isna()]

c1, c2, c3, c4 = st.columns(4)
c1.metric("SKU de Ovella", len(ovella_df))
c2.metric("Competidores monitoreados", len(df))
c3.metric("En oferta", len(con_descuento), f"{len(ofertas_agresivas)} con descuento ≥20%", delta_color="off")
c4.metric("Sin dato reciente", len(pendientes))

st.divider()

usados = set()
for _, ov in ovella_df.iterrows():
    grupo = df[df["metros_totales"] == ov["metros_totales"]]
    usados.update(grupo.index)
    with st.container(border=True):
        st.subheader(f"🧻 Ovella — {ov['producto']} ({int(ov['metros_totales'])} m totales)")
        if grupo.empty:
            st.caption("Todavía no hay competidores monitoreados con este mismo metraje.")
            continue

        validos = grupo[grupo["precio_metro"].notna()]
        if not validos.empty:
            fila_barata = validos.loc[validos["precio_metro"].idxmin()]
            fila_cara = validos.loc[validos["precio_metro"].idxmax()]
            promedio = round(validos["precio_metro"].mean(), 1)
            m1, m2, m3 = st.columns(3)
            m1.metric("Más barato ($/m)", f"${fila_barata['precio_metro']}",
                      help=f"{fila_barata['marca']} — {fila_barata['retailer_nombre']}")
            m2.metric("Promedio ($/m)", f"${promedio}")
            m3.metric("Más caro ($/m)", f"${fila_cara['precio_metro']}",
                      help=f"{fila_cara['marca']} — {fila_cara['retailer_nombre']}")

        _mostrar_tabla(grupo)

sin_match = df[~df.index.isin(usados)]
if not sin_match.empty:
    with st.expander(f"📦 Otros {len(sin_match)} productos monitoreados (sin formato Ovella equivalente)"):
        st.caption("Mismo metraje que ningún SKU en ovella.csv — agrégalo ahí si corresponde a un formato propio.")
        _mostrar_tabla(sin_match)

if "fecha_act" in df.columns and df["fecha_act"].notna().any():
    ultima_fecha = df["fecha_act"].dropna().max()
    st.caption(f"Última actualización de precios: {ultima_fecha} · se actualiza automáticamente vía GitHub Actions.")
