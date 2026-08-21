import streamlit as st
import pandas as pd
import json
from pathlib import Path

# --- CONFIGURACIÓN DE COLORES Y PÁGINA ---
COLOR_BUENO = "#0ca30c"
COLOR_CRITICO = "#d03b3b"
COLOR_ACENTO = "#2a78d6"

st.set_page_config(page_title="Dipisa & Ovella — Monitor de Pricing", page_icon="📊", layout="wide")

# --- RUTAS DE ARCHIVOS ---
BASE_DIR = Path(__file__).parent
DATOS_PATH = BASE_DIR / "datos_procesados.json"
PRODUCTOS_PATH = BASE_DIR / "productos.csv"
OVELLA_PATH = BASE_DIR / "ovella.csv"
RETAILERS_PATH = BASE_DIR / "retailers.yaml"

# --- 1. CARGA DE BASES DE DATOS (Con protección de filas vacías) ---
try:
    productos_df = pd.read_csv(PRODUCTOS_PATH, comment="#", skip_blank_lines=True).dropna(subset=["sku_interno"])
    ovella_df = pd.read_csv(OVELLA_PATH, comment="#", skip_blank_lines=True).dropna(subset=["sku_ovella"])
    
    import yaml
    with open(RETAILERS_PATH, "r", encoding="utf-8") as f:
        retailers_cfg = yaml.safe_load(f)
except Exception as e:
    st.error(f"Error cargando archivos base CSV/YAML: {e}")
    st.stop()

# --- 2. CARGA DE PRECIOS DEL BOT DE GITHUB ---
try:
    with open(DATOS_PATH, "r", encoding="utf-8") as f:
        precios_bot = json.load(f)
except FileNotFoundError:
    precios_bot = []
    st.warning("⚠️ El bot de GitHub aún no ha ejecutado su primer escaneo o no encontró el archivo.")

# --- 3. CRUCE DE DATOS ---
datos_tabla = []
ultima_act = "Desconocida"
if precios_bot:
    ultima_act = max(p.get("fecha_act", "") for p in precios_bot if "fecha_act" in p)

dict_precios = {p["sku_interno"]: p for p in precios_bot}

for _, info in productos_df.iterrows():
    sku = info["sku_interno"]
    cfg = retailers_cfg.get(info["retailer"], {})
    
    # Proteger conversión matemática de metros
    try:
        metros_totales_num = float(info["metros_totales"])
    except (ValueError, TypeError):
        metros_totales_num = 0

    if sku in dict_precios:
        p_bot = dict_precios[sku]
        precio = p_bot["precio"]
        precio_metro = round(precio / metros_totales_num, 1) if metros_totales_num > 0 else 0
        datos_tabla.append({
            **info.to_dict(),
            "metros_num_clean": metros_totales_num,
            "retailer_nombre": cfg.get("nombre", info["retailer"]),
            "precio_oferta_num": precio,
            "precio_normal_num": p_bot["precio_normal"],
            "precio_metro_num": precio_metro,
            "estado": p_bot["estado"]
        })
    else:
        # Si el bot no logró sacar el precio, mostrar "Sin Conexión"
        datos_tabla.append({
            **info.to_dict(),
            "metros_num_clean": metros_totales_num,
            "retailer_nombre": cfg.get("nombre", info["retailer"]),
            "precio_oferta_num": 0,
            "precio_normal_num": 0,
            "precio_metro_num": 0,
            "estado": "❌ Sin Conexión / Esperando bot"
        })

# --- 4. AGRUPACIÓN POR FORMATO DE OVELLA ---
grupos = []
usados = set()
for _, ov in ovella_df.iterrows():
    try:
        metros_ovella = float(ov["metros_totales"])
    except (ValueError, TypeError):
        continue # Ignorar si la fila no tiene metros válidos

    # Cruzar competidores que tengan exactamente los mismos metros
    competidores = [d for d in datos_tabla if d["metros_num_clean"] == metros_ovella]
    competidores_ordenados = sorted(competidores, key=lambda d: (d["precio_metro_num"] <= 0, d["precio_metro_num"]))
    usados.update(id(d) for d in competidores)
    grupos.append({"ovella": ov, "competidores": competidores_ordenados})

sin_match = [d for d in datos_tabla if id(d) not in usados]

# --- 5. INTERFAZ GRÁFICA (UI) ---
st.markdown(f"""<style>[data-testid="stMetricValue"] {{ font-size: 1.4rem; }} h3 {{ color: {COLOR_ACENTO}; }}</style>""", unsafe_allow_html=True)
st.title("📊 Dipisa & Ovella — Monitor de Pricing en Vivo")
st.caption("Arquitectura Autónoma: Actualizado por Robot de GitHub. Carga instantánea y sin bloqueos.")

st.info(f"🕒 Último informe generado por el bot: **{ultima_act}**")

# Métricas superiores
c1, c2, c3, c4 = st.columns(4)
c1.metric("SKU de Ovella", len(grupos))
c2.metric("SKU monitoreados", len(datos_tabla))
c3.metric("Sin stock detectado", len([d for d in datos_tabla if "Sin Stock" in str(d["estado"])]))
c4.metric("Sin Conexión / Error", len([d for d in datos_tabla if "❌" in str(d["estado"])]))

st.divider()

def _formatear_clp(valor): 
    return f"${valor:,.0f}".replace(",", ".")

def renderizar_tabla(competidores):
    filas = []
    for c in competidores:
        normal, oferta = c["precio_normal_num"], c["precio_oferta_num"]
        en_oferta = bool(oferta and normal and oferta < normal)
        desc_pct = round((1 - oferta / normal) * 100) if en_oferta else None
        filas.append({
            "Retailer": c["retailer_nombre"], 
            "Marca": c["marca"], 
            "Producto": c["producto"],
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
    
    # Resaltar colores de filas
    def resaltar(fila):
        if fila["$/Metro"] == minimo and minimo is not None: 
            return [f"background-color: {COLOR_BUENO}26"] * len(fila)
        if "Sin Stock" in str(fila["Estado"]) or "⚠️" in str(fila["Estado"]) or "❌" in str(fila["Estado"]): 
            return [f"background-color: {COLOR_CRITICO}1a"] * len(fila)
        return [""] * len(fila)
    
    return df.style.apply(resaltar, axis=1).format({"$/Metro": lambda v: f"${v}/m" if pd.notna(v) else "N/D"})

# Renderizar grupos de Ovella
for grupo in grupos:
    ov = grupo["ovella"]
    competidores = grupo["competidores"]
    with st.container(border=True):
        
        # Proteger encabezado contra textos rotos
        try:
            metros_val = int(float(ov["metros_totales"]))
            metros_str = f"{metros_val} m totales"
        except (ValueError, TypeError):
            metros_str = "N/D m totales"

        nombre_prod = ov.get("producto", "Producto sin nombre")
        st.subheader(f"🧻 Ovella — {nombre_prod} ({metros_str})")
        
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
            except Exception: 
                pass
        else:
            st.caption("Todavía no hay competidores con este metraje.")

# Renderizar huérfanos
if sin_match:
    with st.expander(f"📦 Otros {len(sin_match)} productos monitoreados"):
        st.caption("Productos de tu CSV que no calzan exactamente con los metros de un SKU de Ovella.")
        try: 
            st.dataframe(renderizar_tabla(sin_match), use_container_width=True, hide_index=True)
        except Exception: 
            pass
