"""Panel de Convenio Marco (Mercado Público) — papel tissue, todas las regiones.

Lee los CSV que deja convenio_marco.py (corre solo en GitHub Actions, ver
.github/workflows/convenio_marco.yml). Acá no se scrapea nada: solo se muestra.
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))
from convenio_marco import COLS_CAMBIOS, DTYPES_CSV, REGION_REF, REGIONES, a_largo  # noqa: E402

st.set_page_config(page_title="Convenio Marco | Dipisa", page_icon="🧻", layout="wide")

ARCHIVO_PROVEEDORES = BASE_DIR / "convenio_marco_proveedores.csv"
ARCHIVO_CAMBIOS = BASE_DIR / "convenio_marco_cambios.csv"
ARCHIVO_ESTADO = BASE_DIR / "estado_convenio_marco.json"
DIAS_ALERTA = 10  # sin corrida nueva en tantos días -> se avisa
REGION_POR_NOMBRE = {nombre: rid for rid, nombre in REGIONES.items()}


def clp(v):
    return "" if pd.isna(v) else "$" + f"{int(v):,}".replace(",", ".")


def dias_txt(v):
    """Días como número entero (10, no 10.0 ni 10.000000); vacío si no hay dato."""
    return "" if pd.isna(v) else str(int(v))


def clp_vacio(v):
    return "—" if pd.isna(v) else clp(v)


def tipo_producto(nombre):
    n = str(nombre).upper()
    for clave, etiqueta in [("PAPEL HIGIENICO", "Papel higiénico"), ("TOALLA", "Toalla de papel"),
                            ("SERVILLETA", "Servilletas"), ("SABANILLA", "Sabanilla"),
                            ("PAÑUELO", "Pañuelos")]:
        if n.startswith(clave):
            return etiqueta
    return "Otros"


@st.cache_data(ttl=1800)
def cargar():
    prov = pd.read_csv(ARCHIVO_PROVEEDORES, dtype=DTYPES_CSV)
    cambios = (pd.read_csv(ARCHIVO_CAMBIOS, dtype=str) if ARCHIVO_CAMBIOS.exists()
               else pd.DataFrame(columns=COLS_CAMBIOS))
    for col in COLS_CAMBIOS:  # archivos viejos sin la columna de regiones
        if col not in cambios.columns:
            cambios[col] = ""
    estado = json.loads(ARCHIVO_ESTADO.read_text(encoding="utf-8")) if ARCHIVO_ESTADO.exists() else {}
    largo = a_largo(prov)
    tipos = prov.drop_duplicates("producto_id").set_index("producto_id")["producto"].map(tipo_producto)
    largo["tipo"] = largo["producto_id"].map(tipos)
    return prov, largo, cambios.fillna(""), estado


def descarga_ancha(prov):
    """El formato ancho con encabezados legibles (Precio <región> / Días <región>)."""
    nombres = {}
    for rid, nombre in REGIONES.items():
        nombres[f"p_{rid}"] = f"Precio {nombre}"
        nombres[f"d_{rid}"] = f"Días {nombre}"
    return prov.rename(columns={**nombres, "producto_id": "ID producto", "producto": "Producto",
                                "proveedor_id": "ID proveedor", "proveedor": "Proveedor", "rut": "RUT"})


st.title("🏛️ Convenio Marco — Papel tissue")
st.caption("Precios netos (sin IVA) de los proveedores del Convenio Marco de Mercado Público, "
           "por región de despacho. Se actualiza una vez por semana.")

if not ARCHIVO_PROVEEDORES.exists():
    st.info("Todavía no hay datos. Aparecen después de la primera corrida automática "
            "(GitHub → Actions → «Convenio Marco» → Run workflow).")
    st.stop()

prov, largo, cambios, estado = cargar()
if largo.empty:
    st.info("Los datos guardados están en el formato antiguo (solo Región Metropolitana). "
            "Se actualizan solos en la próxima corrida automática.")
    st.stop()

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

# --- región ------------------------------------------------------------------
nombres_region = list(REGION_POR_NOMBRE)
region_nombre = st.selectbox("📍 Región de despacho", nombres_region,
                             index=nombres_region.index(REGIONES[REGION_REF]), key="region_sel")
rid = REGION_POR_NOMBRE[region_nombre]
lr = largo[largo["region_id"] == rid]

c1, c2, c3 = st.columns(3)
c1.metric("Productos con despacho", lr["producto_id"].nunique(),
          help=f"De {largo['producto_id'].nunique()} productos del convenio, los que tienen "
               f"al menos un proveedor que despacha a esta región.")
c2.metric("Proveedores", lr["proveedor_id"].nunique())
ultima_fecha = cambios["fecha"].max() if len(cambios) else None
c3.metric("Cambios recientes",
          int((cambios["fecha"] == ultima_fecha).sum()) if ultima_fecha else 0,
          help="Cambios detectados en la última corrida semanal (todas las regiones)")

tab_prod, tab_cambios, tab_prov = st.tabs(["📦 Por producto", "🔔 Qué cambió", "🏢 Por proveedor"])

# --- Por producto ----------------------------------------------------------
with tab_prod:
    f1, f2 = st.columns([1, 2])
    tipos = ["Todos"] + sorted(lr["tipo"].dropna().unique())
    tipo_sel = f1.selectbox("Tipo de producto", tipos)
    busqueda = f2.text_input("Buscar por nombre o marca", placeholder="ej. ovella, elite, 4 rollos…")
    base = lr if tipo_sel == "Todos" else lr[lr["tipo"] == tipo_sel]
    # Cada palabra tiene que aparecer en el nombre, en cualquier orden
    # ("ovella 4 rollos 30" encuentra "OVELLA DOBLE HOJA 4 ROLLOS 30 M").
    for palabra in busqueda.split():
        base = base[base["producto"].str.contains(palabra, case=False, na=False, regex=False)]

    resumen = (base.groupby(["producto_id", "producto"])["precio_neto"]
               .agg(proveedores="count", minimo="min", mediana="median", maximo="max")
               .reset_index().sort_values("producto"))
    if resumen.empty:
        st.info("No hay productos con ese filtro en esta región.")
    else:
        st.caption(f"{len(resumen)} producto(s) con despacho a {region_nombre}.")
        vista = resumen.rename(columns={"producto_id": "ID", "producto": "Producto",
                                        "proveedores": "Proveedores", "minimo": "Más barato",
                                        "mediana": "Mediana", "maximo": "Más caro"}).copy()
        for col in ["Más barato", "Mediana", "Más caro"]:
            vista[col] = vista[col].map(clp)
        # La clave cambia con los filtros: al buscar o cambiar de región se limpia la
        # selección y no queda apuntando a otra fila.
        clave_tabla = f"tabla_{rid}_{tipo_sel}_{busqueda.strip().lower()}"
        evento = st.dataframe(vista, hide_index=True, width="stretch", height=280,
                              on_select="rerun", selection_mode="single-row", key=clave_tabla)
        filas_sel = evento.selection.rows if evento and evento.selection else []
        if filas_sel:
            fila_sel = resumen.iloc[filas_sel[0]]
        elif len(resumen) == 1:
            fila_sel = resumen.iloc[0]  # una sola coincidencia: se muestra directo
        else:
            fila_sel = None
        if fila_sel is None:
            st.info("👆 Haz clic en un producto de la tabla para ver sus proveedores.")
        else:
            pid = fila_sel["producto_id"]
            st.subheader(fila_sel["producto"])
            det = (lr[lr["producto_id"] == pid]
                   .sort_values("precio_neto")[["proveedor", "rut", "precio_neto", "dias_entrega"]]
                   .reset_index(drop=True))
            minimo = det["precio_neto"].min()
            det_vista = det.rename(columns={"proveedor": "Proveedor", "rut": "RUT",
                                            "precio_neto": "Precio neto", "dias_entrega": "Días de entrega"})
            det_vista["Precio neto"] = det_vista["Precio neto"].map(clp)
            det_vista["Días de entrega"] = det_vista["Días de entrega"].map(dias_txt)
            det_vista.index = det_vista.index + 1
            st.dataframe(
                det_vista.style.apply(
                    lambda fila: ["background-color: #0ca30c26" if det.loc[fila.name - 1, "precio_neto"] == minimo
                                  else "" for _ in fila], axis=1),
                width="stretch")
            st.caption("Verde = el más barato. Los precios son netos (sin IVA).")

            with st.expander("🗺️ Ver este producto en todas las regiones"):
                todas = largo[largo["producto_id"] == pid]
                orden = [r for r in REGIONES if r in set(todas["region_id"])]
                resumen_reg = []
                for r in orden:
                    t = todas[todas["region_id"] == r].sort_values("precio_neto")
                    resumen_reg.append({"Región": REGIONES[r], "Proveedores": len(t),
                                        "Más barato": clp(t.iloc[0]["precio_neto"]),
                                        "Proveedor más barato": t.iloc[0]["proveedor"]})
                st.markdown("**El más barato de cada región**")
                st.dataframe(pd.DataFrame(resumen_reg), hide_index=True, width="stretch")
                st.markdown("**Precio de cada proveedor por región** (verde = el más barato de la región)")
                mat = todas.pivot_table(index="proveedor", columns="region_id", values="precio_neto", aggfunc="min")
                mat = mat.reindex(columns=orden).rename(columns=REGIONES)
                mat = mat.loc[mat.min(axis=1).sort_values().index]
                st.dataframe(mat.style.format(clp_vacio).highlight_min(axis=0, color="#0ca30c40"),
                             width="stretch")


# --- Qué cambió ------------------------------------------------------------
with tab_cambios:
    if cambios.empty:
        st.info("Todavía no hay cambios registrados: aparecen desde la segunda corrida en adelante.")
    else:
        g1, g2, g3 = st.columns([2, 1, 1])
        tipos_c = sorted(cambios["tipo"].unique())
        sel = g1.multiselect("Tipo de cambio", tipos_c, default=tipos_c)
        fechas = sorted(cambios["fecha"].unique(), reverse=True)
        fecha_sel = g2.selectbox("Corrida", ["Todas"] + fechas)
        region_c = g3.selectbox("Región", ["Todas"] + nombres_region)
        v = cambios[cambios["tipo"].isin(sel)]
        if fecha_sel != "Todas":
            v = v[v["fecha"] == fecha_sel]
        if region_c != "Todas":
            v = v[v["regiones"].str.contains(region_c, regex=False)]
        st.dataframe(
            v.sort_values("fecha", ascending=False)
             .rename(columns={"fecha": "Fecha", "tipo": "Tipo", "producto": "Producto",
                              "proveedor": "Proveedor", "rut": "RUT", "regiones": "Regiones",
                              "detalle": "Detalle"})
             [["Fecha", "Tipo", "Producto", "Proveedor", "RUT", "Regiones", "Detalle"]],
            hide_index=True, width="stretch")

# --- Por proveedor ---------------------------------------------------------
with tab_prov:
    por_prov = (lr.groupby(["proveedor_id", "proveedor", "rut"])
                .agg(productos=("producto_id", "nunique")).reset_index())
    es_min = lr["precio_neto"] == lr.groupby("producto_id")["precio_neto"].transform("min")
    por_prov = por_prov.merge(
        es_min.groupby(lr["proveedor_id"]).sum().rename("mas_barato_en").reset_index(),
        on="proveedor_id")
    st.caption(f"Proveedores que despachan a {region_nombre}: cuántos productos vende cada uno "
               "y en cuántos tiene el precio más bajo.")
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
        d = lr[lr["proveedor_id"] == p["proveedor_id"]][["producto", "precio_neto", "dias_entrega"]]
        d = d.assign(precio_neto=d["precio_neto"].map(clp),
                     dias_entrega=d["dias_entrega"].map(dias_txt)).rename(
            columns={"producto": "Producto", "precio_neto": "Precio neto", "dias_entrega": "Días de entrega"})
        st.dataframe(d, hide_index=True, width="stretch")

st.divider()
d1, d2 = st.columns(2)
d1.download_button(f"⬇️ Descargar {region_nombre} (CSV)",
                   data=lr[["producto_id", "producto", "proveedor", "rut", "precio_neto", "dias_entrega"]]
                   .astype({"dias_entrega": "Int64"})
                   .rename(columns={"producto_id": "ID producto", "producto": "Producto",
                                    "proveedor": "Proveedor", "rut": "RUT",
                                    "precio_neto": "Precio neto", "dias_entrega": "Días de entrega"})
                   .to_csv(index=False).encode("utf-8-sig"),
                   file_name=f"convenio_marco_{region_nombre.lower().replace(' ', '_')}.csv", mime="text/csv")
d2.download_button("⬇️ Descargar todas las regiones (CSV)",
                   data=descarga_ancha(prov).to_csv(index=False).encode("utf-8-sig"),
                   file_name="convenio_marco_todas_las_regiones.csv", mime="text/csv")
