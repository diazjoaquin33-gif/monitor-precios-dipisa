"""Panel compartido de Convenio Marco (Mercado Público): lo muestran las páginas de pages/.

Lee los CSV que deja convenio_marco.py (corre solo en GitHub Actions, ver
.github/workflows/convenio_marco.yml). Acá no se scrapea nada: solo se muestra.
Para agregar un catálogo nuevo: entrada en CATALOGOS (convenio_marco.py), su tipo
y textos en TIPOS / UI de este archivo, y una página nueva en pages/.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from convenio_marco import (CATALOGOS, COLS_CAMBIOS, DTYPES_CSV, REGION_REF, REGIONES, a_largo, rutas)

DIAS_ALERTA = 10  # sin corrida nueva en tantos días -> se avisa
REGION_POR_NOMBRE = {nombre: rid for rid, nombre in REGIONES.items()}


def clp(v):
    return "" if pd.isna(v) else "$" + f"{int(v):,}".replace(",", ".")


def dias_txt(v):
    """Días como número entero (10, no 10.0 ni 10.000000); vacío si no hay dato."""
    return "" if pd.isna(v) else str(int(v))


def clp_vacio(v):
    return "—" if pd.isna(v) else clp(v)


def tipo_tissue(nombre):
    n = str(nombre).upper()
    for clave, etiqueta in [("PAPEL HIGIENICO", "Papel higiénico"), ("TOALLA", "Toalla de papel"),
                            ("SERVILLETA", "Servilletas"), ("SABANILLA", "Sabanilla"),
                            ("PAÑUELO", "Pañuelos")]:
        if n.startswith(clave):
            return etiqueta
    return "Otros"


def tipo_resma(nombre):
    n = str(nombre).upper()
    for clave, etiqueta in [("OFICIO", "Oficio"), ("CARTA", "Carta"), ("A4", "A4"), ("A3", "A3")]:
        if clave in n:
            return etiqueta
    return "Otros"


# Cómo se agrupan los productos de cada catálogo en el filtro "Tipo de producto".
TIPOS = {"tissue": tipo_tissue, "resmas": tipo_resma}
UI = {
    "tissue": {"buscar": "ej. ovella, elite, 4 rollos…", "buscar_comparar": "ej. ovella 4 rollos, elite doble hoja…"},
    "resmas": {"buscar": "ej. chamex, carta, oficio, ecológico…", "buscar_comparar": "ej. chamex carta, oficio blanco…"},
}


@st.cache_data(ttl=1800)
def cargar(clave):
    f_prov, f_cambios, f_estado = rutas(clave)
    prov = pd.read_csv(f_prov, dtype=DTYPES_CSV)
    for col in ("proveedor", "rut"):  # vacío en vez de NaN: si no, los agrupa mal
        prov[col] = prov[col].fillna("")
    cambios = (pd.read_csv(f_cambios, dtype=str) if f_cambios.exists()
               else pd.DataFrame(columns=COLS_CAMBIOS))
    for col in COLS_CAMBIOS:  # archivos viejos sin la columna de regiones
        if col not in cambios.columns:
            cambios[col] = ""
    estado = json.loads(f_estado.read_text(encoding="utf-8")) if f_estado.exists() else {}
    largo = a_largo(prov)
    tipos = prov.drop_duplicates("producto_id").set_index("producto_id")["producto"].map(TIPOS[clave])
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


def mostrar(clave):
    """Dibuja el panel completo del catálogo `clave` (ver CATALOGOS)."""
    cfg = CATALOGOS[clave]
    cfg_ui = UI[clave]
    f_prov, _f_cambios, _f_estado = rutas(clave)

    def K(nombre):
        """Clave de estado propia de cada catálogo (las páginas comparten session_state)."""
        return f"{clave}_{nombre}"

    st.title(f"🏛️ Convenio Marco — {cfg['nombre']}")
    st.caption("Precios netos (sin IVA) de los proveedores del Convenio Marco de Mercado Público, "
               "por región de despacho. Se actualiza una vez por semana.")

    if not f_prov.exists():
        st.info("Todavía no hay datos. Aparecen después de la primera corrida automática "
                "(GitHub → Actions → «Convenio Marco» → Run workflow).")
        st.stop()

    prov, largo, cambios, estado = cargar(clave)
    hay_rut = bool((prov["rut"] != "").any())  # algunos convenios no publican el RUT
    cols_cambios = ["Fecha", "Tipo", "Producto", "Proveedor"] + (["RUT"] if hay_rut else []) + ["Regiones", "Detalle"]
    cols_prov = ["Proveedor"] + (["RUT"] if hay_rut else []) + ["Productos que vende", "Más barato en"]
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

    tab_prod, tab_comp, tab_cambios, tab_prov = st.tabs(
        ["📦 Por producto", "⚖️ Comparar", "🔔 Qué cambió", "🏢 Por proveedor"])

    # Lista de productos del comparador (ids). Se llena desde el botón de "Por producto"
    # o directamente en la pestaña "Comparar".
    st.session_state.setdefault(K("comparar"), [])


    def _agregar_varios(ids_nuevos):
        """Agrega varios productos a la lista y limpia las casillas de la tabla
        (cambiando su clave). Va como on_click: así el valor guardado no se pierde."""
        st.session_state[K("comparar")] = st.session_state[K("comparar")] + [
            i for i in ids_nuevos if i not in st.session_state[K("comparar")]]
        st.session_state[K("comp_n")] = st.session_state.get(K("comp_n"), 0) + 1


    def _agregar_al_comparador(pid_nuevo):
        if pid_nuevo not in st.session_state[K("comparar")]:
            st.session_state[K("comparar")] = st.session_state[K("comparar")] + [pid_nuevo]

    # --- Por producto ----------------------------------------------------------
    with tab_prod:
        f1, f2 = st.columns([1, 2])
        tipos = ["Todos"] + sorted(lr["tipo"].dropna().unique())
        tipo_sel = f1.selectbox("Tipo de producto", tipos)
        busqueda = f2.text_input("Buscar por nombre o marca", placeholder=cfg_ui["buscar"])
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
            evento = st.dataframe(vista, hide_index=True, width="stretch", height=280,
                                  on_select="rerun", selection_mode="single-row", key=K("tabla_productos"))

            opciones = {f"{r.producto} (ID {r.producto_id})": r.producto_id for r in resumen.itertuples()}
            etiquetas = list(opciones)
            # Un clic en la tabla de arriba elige ese producto en el selector de abajo
            # (solo cuando el clic cambia; así el selector sigue siendo usable a mano).
            filas_sel = evento.selection.rows if evento and evento.selection else []
            if filas_sel:
                clic = etiquetas[filas_sel[0]]
                if st.session_state.get(K("_ultimo_clic")) != clic:
                    st.session_state[K("_ultimo_clic")] = clic
                    st.session_state[K("producto_elegido")] = clic
            else:
                st.session_state.pop(K("_ultimo_clic"), None)
            if st.session_state.get(K("producto_elegido")) not in opciones:
                st.session_state.pop(K("producto_elegido"), None)  # el buscador de arriba lo dejó fuera
            st.caption("Haz clic en un producto de la tabla (o elígelo en la lista) para ver sus proveedores. "
                       "La lista solo muestra los productos que deja el buscador de arriba.")
            elegido = st.selectbox("Ver proveedores de:", etiquetas, key=K("producto_elegido"))
            pid = opciones[elegido]
            det = (lr[lr["producto_id"] == pid]
                   .sort_values("precio_neto")[["proveedor", "rut", "precio_neto", "dias_entrega"]]
                   .reset_index(drop=True))
            minimo = det["precio_neto"].min()
            det_vista = det.rename(columns={"proveedor": "Proveedor", "rut": "RUT",
                                            "precio_neto": "Precio neto", "dias_entrega": "Días de entrega"})
            if not hay_rut:
                det_vista = det_vista.drop(columns="RUT")
            det_vista["Precio neto"] = det_vista["Precio neto"].map(clp)
            det_vista["Días de entrega"] = det_vista["Días de entrega"].map(dias_txt)
            det_vista.index = det_vista.index + 1
            st.dataframe(
                det_vista.style.apply(
                    lambda fila: ["background-color: #0ca30c26" if det.loc[fila.name - 1, "precio_neto"] == minimo
                                  else "" for _ in fila], axis=1),
                width="stretch")
            st.caption("Verde = el más barato. Los precios son netos (sin IVA).")
            en_lista = pid in st.session_state[K("comparar")]
            st.button("✅ Ya está en el comparador" if en_lista else "⚖️ Agregar al comparador",
                      key=K(f"agregar_{pid}"), disabled=en_lista, on_click=_agregar_al_comparador, args=(pid,))

            with st.expander("🗺️ Ver este producto en todas las regiones"):
                todas = largo[largo["producto_id"] == pid]
                orden = [r for r in REGIONES if r in set(todas["region_id"])]
                st.caption("Precio neto de cada proveedor por región. Verde = el más barato de esa región.")
                mat = todas.pivot_table(index="proveedor", columns="region_id", values="precio_neto", aggfunc="min")
                mat = mat.reindex(columns=orden).rename(columns=REGIONES)
                mat = mat.loc[mat.min(axis=1).sort_values().index]
                st.dataframe(mat.style.format(clp_vacio).highlight_min(axis=0, color="#0ca30c40"),
                             width="stretch")


    # --- Comparar ---------------------------------------------------------------
    with tab_comp:
        st.caption(f"Compara el **precio del proveedor más barato** de varios productos en "
                   f"**{region_nombre}**. Busca, marca las casillas de los que quieras y agrégalos "
                   "todos juntos; la búsqueda se puede repetir las veces que haga falta sin perder lo ya agregado.")
        nombre_de = largo.drop_duplicates("producto_id").set_index("producto_id")["producto"].sort_values()

        st.markdown("**1. Busca y marca los productos**")
        busq_c = st.text_input("Buscar productos", key=K("busq_comparar"),
                               placeholder=cfg_ui["buscar_comparar"])
        cand = nombre_de.reset_index()
        cand.columns = ["producto_id", "producto"]
        # Cada palabra tiene que aparecer en el nombre, en cualquier orden.
        for palabra in busq_c.split():
            cand = cand[cand["producto"].str.contains(palabra, case=False, na=False, regex=False)]
        cand = cand.reset_index(drop=True)
        minimos = lr.groupby("producto_id")["precio_neto"].min()
        vista_cand = pd.DataFrame({
            "ID": cand["producto_id"], "Producto": cand["producto"],
            "Precio más bajo": cand["producto_id"].map(minimos).map(clp),
            "En el comparador": cand["producto_id"].map(lambda i: "✅" if i in st.session_state[K("comparar")] else ""),
        })
        st.session_state.setdefault(K("comp_n"), 0)
        # La clave cambia al buscar otra cosa o al agregar: así las casillas se limpian solas.
        clave_c = f"{clave}_tabla_comparar_{st.session_state[K('comp_n')]}_{busq_c.strip().lower()}"
        ev_c = st.dataframe(vista_cand, hide_index=True, width="stretch", height=260,
                            on_select="rerun", selection_mode="multi-row", key=clave_c)
        filas_c = ev_c.selection.rows if ev_c and ev_c.selection else []
        marcados = [cand.iloc[i]["producto_id"] for i in filas_c]
        st.caption(f"{len(cand)} resultado(s). Marca con las casillas de la izquierda (puedes marcar varios).")
        nuevos = [m for m in marcados if m not in st.session_state[K("comparar")]]
        etiqueta_btn = (f"➕ Agregar {len(nuevos)} producto(s) al comparador" if nuevos
                        else "➕ Agregar los marcados al comparador")
        st.button(etiqueta_btn, disabled=not nuevos, key=K("btn_agregar_marcados"),
                  on_click=_agregar_varios, args=(nuevos,))

        st.markdown("**2. Productos en el comparador** (quita uno con la ✕; hacen falta al menos 2)")
        elegidos = st.multiselect("Productos en el comparador", options=list(nombre_de.index),
                                  format_func=lambda i: nombre_de.get(i, i), key=K("comparar"),
                                  label_visibility="collapsed",
                                  placeholder="Todavía no hay productos: márcalos arriba y agrégalos.")
        if len(elegidos) < 2:
            st.info("Elige al menos 2 productos para compararlos.")
        else:
            filas = []
            for i in elegidos:
                t = lr[lr["producto_id"] == i].sort_values(["precio_neto", "dias_entrega"])
                if t.empty:
                    filas.append({"id": i, "producto": nombre_de[i], "precio": pd.NA,
                                  "proveedor": "Sin despacho a esta región", "dias": pd.NA, "n": 0})
                else:
                    m = t.iloc[0]
                    filas.append({"id": i, "producto": nombre_de[i], "precio": m["precio_neto"],
                                  "proveedor": m["proveedor"], "dias": m["dias_entrega"], "n": len(t)})
            comp = pd.DataFrame(filas)
            comp["precio"] = pd.to_numeric(comp["precio"], errors="coerce")
            base_min = comp["precio"].min()
            comp["dif"] = comp["precio"] - base_min
            comp["pct"] = comp["dif"] / base_min * 100
            comp = comp.sort_values("precio", na_position="last").reset_index(drop=True)

            vista_c = pd.DataFrame({
                "Producto": comp["producto"],
                "Precio más bajo": comp["precio"].map(clp),
                "Proveedor": comp["proveedor"],
                "Días": comp["dias"].map(dias_txt),
                "Proveedores": comp["n"],
                "Diferencia": [("—" if pd.isna(d) else ("el más barato" if d == 0 else f"+{clp(d)} (+{p:.0f}%)"))
                               for d, p in zip(comp["dif"], comp["pct"])],
            })
            vista_c.index = vista_c.index + 1
            st.dataframe(
                vista_c.style.apply(
                    lambda fila: ["background-color: #0ca30c26" if comp.loc[fila.name - 1, "dif"] == 0 else ""
                                  for _ in fila], axis=1),
                width="stretch")
            st.caption("Verde = el producto con el precio más bajo. Es el precio del pack completo (no por metro "
                       "ni por unidad), así que conviene comparar packs de tamaño parecido. Precios netos (sin IVA).")
            if comp["precio"].isna().any():
                st.caption("Los productos «sin despacho» no tienen ningún proveedor que llegue a esta región.")

            with st.expander("🗺️ Ver el precio más bajo de estos productos en todas las regiones"):
                sel_largo = largo[largo["producto_id"].isin(elegidos)]
                mat_c = sel_largo.groupby(["producto", "region_id"])["precio_neto"].min().unstack("region_id")
                mat_c = mat_c.reindex(columns=[r for r in REGIONES if r in mat_c.columns]).rename(columns=REGIONES)
                st.caption("Precio más bajo de cada producto en cada región. Verde = el más barato de la región.")
                st.dataframe(mat_c.style.format(clp_vacio).highlight_min(axis=0, color="#0ca30c40"), width="stretch")
        if elegidos and st.button("🧹 Vaciar la lista"):
            st.session_state[K("comparar")] = []
            st.rerun()

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
                 [cols_cambios],
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
        nombre = st.text_input("Buscar proveedor", placeholder="nombre o RUT" if hay_rut else "nombre")
        if nombre.strip():
            m = (por_prov["proveedor"].str.contains(nombre.strip(), case=False, na=False)
                 | por_prov["rut"].str.contains(nombre.strip(), case=False, na=False))
            por_prov = por_prov[m]
        vista_prov = por_prov.sort_values("productos", ascending=False).rename(
            columns={"proveedor": "Proveedor", "rut": "RUT", "productos": "Productos que vende",
                     "mas_barato_en": "Más barato en"})
        st.dataframe(vista_prov[cols_prov],
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
                       .drop(columns=[] if hay_rut else ["RUT"])
                       .to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"convenio_marco_{clave}_{region_nombre.lower().replace(' ', '_')}.csv", mime="text/csv")
    d2.download_button("⬇️ Descargar todas las regiones (CSV)",
                       data=descarga_ancha(prov).drop(columns=[] if hay_rut else ["RUT"]).to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"convenio_marco_{clave}_todas_las_regiones.csv", mime="text/csv")
