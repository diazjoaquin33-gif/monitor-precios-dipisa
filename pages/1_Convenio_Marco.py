"""Panel de Convenio Marco (Mercado Público) — papel tissue, Región Metropolitana.

Lee los CSV que deja convenio_marco.py (corre solo en GitHub Actions, ver
.github/workflows/convenio_marco.yml). Acá no se scrapea nada: solo se muestra.
"""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Convenio Marco | Dipisa", page_icon="🧻", layout="wide")

BASE_DIR = Path(__file__).parent.parent
ARCHIVO_PROVEEDORES = BASE_DIR / "convenio_marco_proveedores.csv"
ARCHIVO_CAMBIOS = BASE_DIR / "convenio_marco_cambios.csv"
ARCHIVO_ESTADO = BASE_DIR / "estado_convenio_marco.json"
DIAS_ALERTA = 10  # sin corrida nueva en tantos días -> se avisa


def clp(v):
    return "" if pd.isna(v) else "$" + f"{int(v):,}".replace(",", ".")


@st.cache_data(ttl=1800)
def cargar():
    prov = pd.read_csv(ARCHIVO_PROVEEDORES, dtype={"producto_id": str, "proveedor_id": str})
    cambios = (pd.read_csv(ARCHIVO_CAMBIOS, dtype=str) if ARCHIVO_CAMBIOS.exists()
               else pd.DataFrame(columns=["fecha", "tipo", "producto_id", "producto",
                                          "proveedor", "rut", "detalle"]))
    estado = json.loads(ARCHIVO_ESTADO.read_text(encoding="utf-8")) if ARCHIVO_ESTADO.exists() else {}
    return prov, cambios, estado


def tipo_producto(nombre):
    n = str(nombre).upper()
    for clave, etiqueta in [("PAPEL HIGIENICO", "Papel higiénico"), ("TOALLA", "Toalla de papel"),
                            ("SERVILLETA", "Servilletas"), ("SABANILLA", "Sabanilla"),
                            ("PAÑUELO", "Pañuelos"), ("PAÑUELOS", "Pañuelos")]:
        if n.startswith(clave):
            return etiqueta
    return "Otros"


st.title("🏛️ Convenio Marco — Papel tissue (Región Metropolitana)")
st.caption("Precios netos (sin IVA) de los proveedores del Convenio Marco de Mercado Público "
           "que despachan a la Región Metropolitana. Se actualiza una vez por semana.")

if not ARCHIVO_PROVEEDORES.exists():
    st.info("Todavía no hay datos. Aparecen después de la primera corrida automática "
            "(GitHub → Actions → «Convenio Marco» → Run workflow).")
    st.stop()

prov, cambios, estado = cargar()
prov["tipo"] = prov["producto"].map(tipo_producto)

# --- estado de la última corrida -------------------------------------------
ultima = estado.get("ultima_corrida")
try:
    # El servidor de Streamlit está en UTC; las fechas guardadas son hora de Chile.
    try:
        ahora = datetime.now(ZoneInfo("America/Santiago")).replace(tzinfo=None)
    except Exception:
        ahora = datetime.now()
    dias = (ahora - datetime.strptime(ultima, "%d/%m/%Y %H:%M")).days
except (TypeError, ValueError):
    dias = None
if estado.get("ok") is False:
    st.error("La última corrida falló (probablemente el sitio cambió). Se muestran los datos de la "
             "corrida anterior. Avisar a quien mantiene el sistema.")
elif dias is not None and dias > DIAS_ALERTA:
    st.warning(f"Los datos tienen {dias} días sin actualizarse. Revisar en GitHub → Actions si la "
               "corrida semanal está funcionando.")
if estado.get("productos_con_error"):
    st.caption(f"⚠️ {estado['productos_con_error']} producto(s) no se pudieron leer en la última corrida.")

if ultima:
    st.caption(f"🕒 Última actualización: **{ultima} hrs** (hora de Chile)")
c1, c2, c3 = st.columns(3)
c1.metric("Productos", prov["producto_id"].nunique())
c2.metric("Proveedores", prov["proveedor_id"].nunique())
ultima_fecha = cambios["fecha"].max() if len(cambios) else None
c3.metric("Cambios recientes",
          int((cambios["fecha"] == ultima_fecha).sum()) if ultima_fecha else 0,
          help="Cambios detectados en la última corrida semanal")

tab_prod, tab_cambios, tab_prov = st.tabs(["📦 Por producto", "🔔 Qué cambió", "🏢 Por proveedor"])

# --- Por producto ----------------------------------------------------------
with tab_prod:
    f1, f2 = st.columns([1, 2])
    tipos = ["Todos"] + sorted(prov["tipo"].unique())
    tipo_sel = f1.selectbox("Tipo de producto", tipos)
    busqueda = f2.text_input("Buscar por nombre o marca", placeholder="ej. ovella, elite, 4 rollos…")
    base = prov if tipo_sel == "Todos" else prov[prov["tipo"] == tipo_sel]
    # Cada palabra tiene que aparecer en el nombre, en cualquier orden
    # ("ovella 4 rollos 30" encuentra "OVELLA DOBLE HOJA 4 ROLLOS 30 M").
    for palabra in busqueda.split():
        base = base[base["producto"].str.contains(palabra, case=False, na=False, regex=False)]

    resumen = (base.groupby(["producto_id", "producto"])["precio_neto"]
               .agg(proveedores="count", minimo="min", mediana="median", maximo="max")
               .reset_index().sort_values("producto"))
    if resumen.empty:
        st.info("No hay productos con ese filtro.")
    else:
        st.caption(f"{len(resumen)} producto(s). Elige uno para ver todos sus proveedores.")
        vista = resumen.rename(columns={"producto_id": "ID", "producto": "Producto",
                                        "proveedores": "Proveedores", "minimo": "Más barato",
                                        "mediana": "Mediana", "maximo": "Más caro"}).copy()
        for col in ["Más barato", "Mediana", "Más caro"]:
            vista[col] = vista[col].map(clp)
        evento = st.dataframe(vista, hide_index=True, width="stretch", height=280,
                              on_select="rerun", selection_mode="single-row", key="tabla_productos")

        opciones = {f"{r.producto} (ID {r.producto_id})": r.producto_id for r in resumen.itertuples()}
        etiquetas = list(opciones)
        # Un clic en la tabla de arriba elige ese producto en el selector de abajo
        # (solo cuando el clic cambia; así el selector sigue siendo usable a mano).
        filas_sel = evento.selection.rows if evento and evento.selection else []
        if filas_sel:
            clic = etiquetas[filas_sel[0]]
            if st.session_state.get("_ultimo_clic") != clic:
                st.session_state["_ultimo_clic"] = clic
                st.session_state["producto_elegido"] = clic
        else:
            st.session_state.pop("_ultimo_clic", None)
        if st.session_state.get("producto_elegido") not in opciones:
            st.session_state.pop("producto_elegido", None)  # el filtro lo dejó fuera
        st.caption("Haz clic en un producto de la tabla para ver sus proveedores abajo.")
        elegido = st.selectbox("Ver proveedores de:", etiquetas, key="producto_elegido")
        det = (prov[prov["producto_id"] == opciones[elegido]]
               .sort_values("precio_neto")[["proveedor", "rut", "precio_neto", "dias_entrega"]]
               .reset_index(drop=True))
        minimo = det["precio_neto"].min()
        det_vista = det.rename(columns={"proveedor": "Proveedor", "rut": "RUT",
                                        "precio_neto": "Precio neto", "dias_entrega": "Días de entrega"})
        det_vista["Precio neto"] = det_vista["Precio neto"].map(clp)
        det_vista.index = det_vista.index + 1
        st.dataframe(
            det_vista.style.apply(
                lambda fila: ["background-color: #0ca30c26" if det.loc[fila.name - 1, "precio_neto"] == minimo
                              else "" for _ in fila], axis=1),
            width="stretch")
        st.caption("Verde = el más barato. Los precios son netos (sin IVA).")

# --- Qué cambió ------------------------------------------------------------
with tab_cambios:
    if cambios.empty:
        st.info("Todavía no hay cambios registrados: aparecen desde la segunda corrida en adelante.")
    else:
        tipos_c = sorted(cambios["tipo"].unique())
        sel = st.multiselect("Tipo de cambio", tipos_c, default=tipos_c)
        fechas = sorted(cambios["fecha"].unique(), reverse=True)
        fecha_sel = st.selectbox("Corrida", ["Todas"] + fechas)
        v = cambios[cambios["tipo"].isin(sel)]
        if fecha_sel != "Todas":
            v = v[v["fecha"] == fecha_sel]
        st.dataframe(
            v.sort_values("fecha", ascending=False)
             .rename(columns={"fecha": "Fecha", "tipo": "Tipo", "producto": "Producto",
                              "proveedor": "Proveedor", "rut": "RUT", "detalle": "Detalle"})
             [["Fecha", "Tipo", "Producto", "Proveedor", "RUT", "Detalle"]],
            hide_index=True, width="stretch")

# --- Por proveedor ---------------------------------------------------------
with tab_prov:
    por_prov = (prov.groupby(["proveedor_id", "proveedor", "rut"])
                .agg(productos=("producto_id", "nunique")).reset_index())
    min_prod = prov.groupby("producto_id")["precio_neto"].transform("min")
    prov["es_mas_barato"] = prov["precio_neto"] == min_prod
    por_prov = por_prov.merge(
        prov.groupby("proveedor_id")["es_mas_barato"].sum().rename("mas_barato_en").reset_index(),
        on="proveedor_id")
    st.caption("Cuántos productos vende cada proveedor y en cuántos tiene el precio más bajo.")
    nombre = st.text_input("Buscar proveedor", placeholder="nombre o RUT")
    if nombre.strip():
        m = (por_prov["proveedor"].str.contains(nombre.strip(), case=False, na=False)
             | por_prov["rut"].str.contains(nombre.strip(), case=False, na=False))
        por_prov = por_prov[m]
    vista_prov = por_prov.sort_values("productos", ascending=False).rename(
        columns={"proveedor": "Proveedor", "rut": "RUT", "productos": "Productos que vende",
                 "mas_barato_en": "Más barato en"})
    st.dataframe(vista_prov[["Proveedor", "RUT", "Productos que vende", "Más barato en"]],
                 hide_index=True, width="stretch", height=360)

    if len(por_prov) == 1:
        p = por_prov.iloc[0]
        st.subheader(p["proveedor"])
        d = prov[prov["proveedor_id"] == p["proveedor_id"]][["producto", "precio_neto", "dias_entrega"]]
        d = d.assign(precio_neto=d["precio_neto"].map(clp)).rename(
            columns={"producto": "Producto", "precio_neto": "Precio neto", "dias_entrega": "Días de entrega"})
        st.dataframe(d, hide_index=True, width="stretch")

st.divider()
st.download_button("⬇️ Descargar todos los datos (CSV)",
                   data=prov.drop(columns=["tipo", "es_mas_barato"], errors="ignore")
                   .to_csv(index=False).encode("utf-8-sig"),
                   file_name="convenio_marco_proveedores.csv", mime="text/csv")
