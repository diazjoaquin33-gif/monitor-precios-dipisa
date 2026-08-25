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

# +-15% de metraje para considerar dos productos "comparables" dentro de la
# misma categoria+subcategoria — ya no exige metraje idéntico.
RANGO_TOLERANCIA_METROS = 0.15

ICONO_CATEGORIA = {"Papel Higienico": "🧻", "Toalla de Papel": "🧺"}


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
    semántica de datos. Incluye la URL cruda del producto en una columna
    aparte para que column_config la muestre como link clickeable."""
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
            "Ver": r.get("url"),
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


COLUMN_CONFIG = {
    "Ver": st.column_config.LinkColumn("Ver", display_text="Ver ↗", width="small"),
}


def _mostrar_tabla(df_grupo):
    try:
        st.dataframe(_tabla_categoria(df_grupo), width="stretch", hide_index=True, column_config=COLUMN_CONFIG)
    except TypeError:
        st.dataframe(_tabla_categoria(df_grupo), use_container_width=True, hide_index=True, column_config=COLUMN_CONFIG)


# --- Interfaz ---
try:
    df, ovella_df = cargar_datos()
except Exception as e:
    st.error(f"Aún no hay datos procesados o hubo un error al cargar. Ejecuta el Scraper en Actions. Error: {e}")
    st.stop()

ultima_fecha = None
if "fecha_act" in df.columns and df["fecha_act"].notna().any():
    ultima_fecha = df["fecha_act"].dropna().max()

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

# Arma primero los grupos (para saber si hay "otros" antes de crear los tabs).
# Coinciden por categoria+subcategoria EXACTA, y metraje dentro de +-15% del
# SKU de Ovella (no exige metraje idéntico, para no perder competidores de
# formato parecido pero no igual).
grupos = []
usados = set()
for _, ov in ovella_df.iterrows():
    margen = ov["metros_totales"] * RANGO_TOLERANCIA_METROS
    grupo = df[
        (df["categoria"] == ov["categoria"])
        & (df["subcategoria"] == ov["subcategoria"])
        & (df["metros_totales"] >= ov["metros_totales"] - margen)
        & (df["metros_totales"] <= ov["metros_totales"] + margen)
    ]
    usados.update(grupo.index)
    grupos.append((ov, grupo))

sin_match = df[~df.index.isin(usados)]

nombres_tabs = [f"{ICONO_CATEGORIA.get(ov['categoria'], '📄')} {ov['producto']}" for ov, _ in grupos]
if not sin_match.empty:
    nombres_tabs.append(f"📦 Otros ({len(sin_match)})")

tabs = st.tabs(nombres_tabs)

for tab, (ov, grupo) in zip(tabs, grupos):
    with tab:
        st.caption(f"{ov['categoria']} · {ov['subcategoria']} · ~{int(ov['metros_totales'])} m por paquete (rango ±15%)")
        if grupo.empty:
            st.info("Todavía no hay competidores monitoreados con este mismo metraje.")
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

if not sin_match.empty:
    with tabs[-1]:
        st.caption("No calza con ningún SKU de Ovella (categoría/subcategoría distinta, o metraje fuera del rango ±15%) — revisa si corresponde agregar un formato nuevo en ovella.csv.")
        _mostrar_tabla(sin_match)

st.caption("Se actualiza automáticamente 3 veces al día vía GitHub Actions.")
