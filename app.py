import streamlit as st
import pandas as pd
import json
from pathlib import Path

COLOR_BUENO = "#0ca30c"
COLOR_CRITICO = "#d03b3b"
COLOR_ACENTO = "#2a78d6"

st.set_page_config(page_title="Dipisa & Ovella — Monitor de Pricing", page_icon="📊", layout="wide")

BASE_DIR = Path(__file__).parent
DATOS_PATH = BASE_DIR / "datos_procesados.json"
PRODUCTOS_PATH = BASE_DIR / "productos.csv"
OVELLA_PATH = BASE_DIR / "ovella.csv"
RETAILERS_PATH = BASE_DIR / "retailers.yaml"

# 1. Cargar bases y configuración
try:
    productos_df = pd.read_csv(PRODUCTOS_PATH)
    ovella_df = pd.read_csv(OVELLA_PATH).dropna(subset=["sku_ovella"])
    import yaml
    with open(RETAILERS_PATH, "r", encoding="utf-8") as f:
        retailers_cfg = yaml.safe_load(f)
except Exception:
    st.error("Error cargando archivos base CSV/YAML.")
    st.stop()

# 2. Cargar los precios que guardó el bot de GitHub
try:
    with open(DATOS_PATH, "r", encoding="utf-8") as f:
        precios_bot = json.load(f)
except FileNotFoundError:
    precios_bot = []
    st.warning("⚠️ El bot de GitHub aún no ha ejecutado su primer escaneo. Vuelve en un rato.")

# 3. Cruzar datos
datos_tabla = []
ultima_act = "Desconocida"
if precios_bot:
    ultima_act = max(p.get("fecha_act", "") for p in precios_bot)

for p_bot in precios_bot:
    prod_info = productos_df[productos_df["sku_interno"] == p_bot["sku_interno"]]
    if not prod_info.empty:
        info = prod_info.iloc[0]
        cfg = retailers_cfg.get(info["retailer"], {})
        precio_metro = round(p_bot["precio"] / info["metros_totales"], 1) if info.get("metros_totales") else 0
        datos_tabla.append({
            **info.to_dict(),
            "retailer_nombre": cfg.get("nombre", info["retailer"]),
            "precio_oferta_num": p_bot["precio"],
            "precio_normal_num": p_bot["precio_normal"],
            "precio_metro_num": precio_metro,
            "estado": p_bot["estado"]
        })

# 4. Agrupar por Ovella
grupos = []
usados = set()
for _, ov in ovella_df.iterrows():
    competidores = [d for d in datos_tabla if d["metros_totales"] == ov["metros_totales"]]
    competidores_ordenados = sorted(competidores, key=lambda d: (d["precio_metro_num"] <= 0, d["precio_metro_num"]))
    usados.update(id(d) for d in competidores)
    grupos.append({"ovella": ov, "competidores": competidores_ordenados})

sin_match = [d for d in datos_tabla if id(d) not in usados]

# --- UI STREAMLIT ---
st.markdown(f"""<style>[data-testid="stMetricValue"] {{ font-size: 1.4rem; }} h3 {{ color: {COLOR_ACENTO}; }}</style>""", unsafe_allow_html=True)
st.title("📊 Dipisa & Ovella — Monitor de Pricing en Vivo")
st.caption("Arquitectura Autónoma: Actualizado por Robot de GitHub 3 veces al día. Carga instantánea y sin bloqueos.")

st.info(f"🕒 Último informe generado por el bot: **{ultima_act}**")

c1, c2, c3, c4 = st.columns(4)
c1.metric("SKU de Ovella", len(ovella_df))
c2.metric("SKU monitoreados", len(datos_tabla))
c3.metric("Sin stock detectado", len([d for d in datos_tabla if "Sin Stock" in d["estado"]]))
c4.metric("Desactualizados", len([d for d in datos_tabla if "Últ. precio" in d["estado"]]))

st.divider()

def _formatear_clp(valor): return f"${valor:,.0f}".replace(",", ".")

def renderizar_tabla(competidores):
    filas = []
    for c in competidores:
        normal, oferta = c["precio_normal_num"], c["precio_oferta_num"]
        en_oferta = bool(oferta and normal and oferta < normal)
        desc_pct = round((1 - oferta / normal) * 100) if en_oferta else None
        filas.append({
            "Retailer": c["retailer_nombre"], "Marca": c["marca"], "Producto": c["producto"],
            "Precio Lista": _formatear_clp(normal) if normal else "N/D",
            "Precio Oferta": _formatear_clp(oferta) if en_oferta else "—",
            "Desc.": f"-{desc_pct}%" if en_oferta else "—",
            "$/Metro": c["precio_metro_num"] if c["precio_metro_num"] else None,
            "Estado": c["estado"],
        })
    df = pd.DataFrame(filas)
    if df.empty: return None
    precios_validos = df["$/Metro"].dropna()
    minimo = precios_validos.min() if not precios_validos.empty else None
    
    def resaltar(fila):
        if fila["$/Metro"] == minimo and minimo is not None: return [f"background-color: {COLOR_BUENO}26"] * len(fila)
        if "Sin Stock" in str(fila["Estado"]) or "⚠️" in str(fila["Estado"]): return [f"background-color: {COLOR_CRITICO}1a"] * len(fila)
        return [""] * len(fila)
    
    return df.style.apply(resaltar, axis=1).format({"$/Metro": lambda v: f"${v}/m" if pd.notna(v) else "N/D"})

for grupo in grupos:
    ov = grupo["ovella"]
    competidores = grupo["competidores"]
    with st.container(border=True):
        st.subheader(f"🧻 Ovella — {ov['producto']} ({int(ov['metros_totales'])} m totales)")
        if competidores:
            validos = [c for c in competidores if c["precio_metro_num"] > 0]
            if validos:
                mas_barato = min(validos, key=lambda c: c["precio_metro_num"])
                promedio = round(sum(c["precio_metro_num"] for c in validos) / len(validos), 1)
                m1, m2, m3 = st.columns(3)
                m1.metric("Más barato ($/m)", f"${mas_barato['precio_metro_num']}", help=mas_barato['retailer_nombre'])
                m2.metric("Promedio ($/m)", f"${promedio}")
            try:
                st.dataframe(renderizar_tabla(competidores), use_container_width=True, hide_index=True)
            except Exception: pass
        else:
            st.caption("Todavía no hay competidores con este metraje.")

if sin_match:
    with st.expander(f"📦 Otros {len(sin_match)} productos monitoreados"):
        try: st.dataframe(renderizar_tabla(sin_match), use_container_width=True, hide_index=True)
        except Exception: pass
