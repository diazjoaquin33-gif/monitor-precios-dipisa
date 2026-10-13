"""Autocorrección de las filas que el equipo carga en la planilla de Catálogo.

Antes de validar, se arregla lo que se puede deducir sin ambigüedad (mayúsculas
o tildes distintas, "50 m" en una celda numérica, categoría vacía que el nombre
delata, metros_totales que no calza con rollos x metros, nombre estándar
distinto dentro de un mismo grupo...). Lo que no se puede deducir con seguridad
se deja tal cual y se reporta en 'avisos' para que alguien lo revise.

Lo usan scraper.py (aplica sobre productos.csv) y app.py (panel en vivo), así
que no depende de streamlit ni de playwright. Cada corrección queda escrita
como una línea legible: "TC-700: marca 'nova' → 'Nova' (misma marca, distinta
escritura)". La planilla de Google no se modifica: la corrección se vuelve a
calcular en cada lectura, siempre igual."""
import re
import unicodedata
from collections import Counter
from difflib import get_close_matches

import pandas as pd

COLUMNAS_NUMERICAS = ["metros_totales", "rollos", "metros_rollo", "unidades"]
CATEGORIAS_POR_UNIDAD = {"servilletas", "toallitas humedas"}

# palabra del nombre -> clave normalizada de subcategoría. Solo se usa si esa
# subcategoría ya existe en la planilla (no se inventan valores nuevos).
PISTAS_SUBCATEGORIA = [
    (r"\btriple\b", "triple hoja"),
    (r"\bdoble hoja\b|\bdh\b|\bdoble\b", "doble hoja"),
    (r"\bhoja simple\b|\buna hoja\b|\bsimple\b|\bhs\b", "hoja simple"),
    (r"\bdispensador\b|\binterfold\b|\binterfoliad", "dispensador"),
    (r"\bcocktail\b|\bcoctel\b", "cocktail"),
    (r"\bmesa\b", "mesa"),
    (r"\bbebe\b|\bbaby\b|\bbabysec\b|\bemubaby\b", "bebe"),
]
PISTAS_CATEGORIA = [
    (r"servilleta", "servilletas"),
    (r"humed|toallita", "toallitas humedas"),
    (r"toalla|\bpt\b", "toalla de papel"),
    (r"higien|\bph\b", "papel higienico"),
]


def _n(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _vacio(v):
    return pd.isna(v) or str(v).strip() == "" or str(v).strip().lower() == "nan"


def parsear_rollos_metros(nombre):
    """(rollos, metros_por_rollo) sacados del nombre: "22 m 4 un", "4 un 22 m",
    "2 x 26 mts". (None, None) si no se puede."""
    s = str(nombre).lower().replace("mts", "m").replace("metros", "m")
    m3 = re.search(r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*m\b", s)
    if m3:
        return int(m3.group(1)), float(m3.group(2))
    m1 = re.search(r"(\d+(?:\.\d+)?)\s*m\b.*?(\d+)\s*un", s)
    if m1:
        return int(m1.group(2)), float(m1.group(1))
    m2 = re.search(r"(\d+)\s*un.*?(\d+(?:\.\d+)?)\s*m\b", s)
    if m2:
        return int(m2.group(1)), float(m2.group(2))
    return None, None


def _numero_desde_texto(val):
    """'50 m', '50mts', '4 un', '21,3' -> número. None si no hay exactamente un
    número reconocible (ahí no se adivina)."""
    texto = str(val).strip()
    if pd.notna(pd.to_numeric(pd.Series([texto]), errors="coerce")).all():
        return None  # ya es un número, nada que corregir
    if "," in texto and "." not in texto:
        probado = pd.to_numeric(pd.Series([texto.replace(",", ".")]), errors="coerce").iloc[0]
        if pd.notna(probado):
            return float(probado)
    hallados = re.findall(r"\d+(?:[.,]\d+)?", texto)
    if len(hallados) != 1:
        return None
    return float(hallados[0].replace(",", "."))


def _vocabulario(serie):
    """clave normalizada -> escritura más usada en la planilla."""
    por_clave = {}
    for v in serie.dropna():
        if _vacio(v):
            continue
        por_clave.setdefault(_n(v), Counter())[str(v).strip()] += 1
    return {k: c.most_common(1)[0][0] for k, c in por_clave.items()}


def _normalizar_grupo(g):
    try:
        return str(int(float(g)))
    except (TypeError, ValueError):
        return str(g).strip()


def autocorregir_catalogo(df, retailers_validos):
    """Devuelve (df_corregido, correcciones, avisos). 'correcciones' = lo que se
    arregló solo; 'avisos' = lo sospechoso que se dejó igual para revisar."""
    df = df.copy()
    correcciones, avisos = [], []
    if df.empty or "sku_interno" not in df.columns:
        return df, correcciones, avisos
    for col in list(COLUMNAS_NUMERICAS):
        if col in df.columns:
            df[col] = df[col].astype(object)
    for col in ("producto", "marca", "categoria", "subcategoria", "retailer", "url", "nombre_estandar"):
        if col in df.columns:
            df[col] = df[col].astype(object)

    vocab = {c: _vocabulario(df[c]) for c in ("marca", "categoria", "subcategoria") if c in df.columns}
    retailers_por_clave = {_n(r).replace(" ", ""): r for r in retailers_validos}

    def corregir(sku, idx, campo, nuevo, motivo):
        antes = df.at[idx, campo]
        df.at[idx, campo] = nuevo
        correcciones.append(f"{sku}: {campo} '{'' if _vacio(antes) else antes}' → '{nuevo}' ({motivo})")

    for idx in df.index:
        sku = str(df.at[idx, "sku_interno"]).strip()
        if not sku or sku.lower() == "nan":
            continue
        nombre = df.at[idx, "producto"] if "producto" in df.columns else ""
        nombre_n = "" if _vacio(nombre) else _n(nombre)

        # retailer: "Santa Isabel" / "a cuenta" -> clave real
        if "retailer" in df.columns and not _vacio(df.at[idx, "retailer"]):
            actual = str(df.at[idx, "retailer"]).strip()
            real = retailers_por_clave.get(_n(actual).replace(" ", ""))
            if real and real != actual:
                if real.lower() == actual.lower():
                    df.at[idx, "retailer"] = real
                else:
                    corregir(sku, idx, "retailer", real, "misma tienda, distinta escritura")

        # url: espacios, comillas, "www." sin https
        if "url" in df.columns and not _vacio(df.at[idx, "url"]):
            u = str(df.at[idx, "url"]).strip().strip("\"'<>").strip()
            if re.match(r"^(www\.|[a-z0-9-]+\.(cl|com)\b)", u, re.I):
                u = "https://" + u
            if u != str(df.at[idx, "url"]):
                corregir(sku, idx, "url", u, "link con espacios o sin https://")

        # numéricos escritos como texto ("50 m", "4 un")
        for col in COLUMNAS_NUMERICAS:
            if col in df.columns and not _vacio(df.at[idx, col]):
                texto = str(df.at[idx, col]).strip()
                n = _numero_desde_texto(texto)
                if n is None:
                    continue
                if re.fullmatch(r"\d+,\d+", texto):
                    df.at[idx, col] = n  # coma decimal por configuración regional de Google Sheets: no es un error
                else:
                    corregir(sku, idx, col, n, "texto con unidad")

        # texto con vocabulario conocido: misma escritura que el resto de la planilla
        for col, claves in vocab.items():
            valor = df.at[idx, col]
            if _vacio(valor):
                continue
            clave = _n(valor)
            if clave in claves:
                if claves[clave] != str(valor).strip():
                    corregir(sku, idx, col, claves[clave], "misma palabra, distinta escritura")
            else:
                cerca = get_close_matches(clave, list(claves), n=1, cutoff=0.84)
                if cerca:
                    corregir(sku, idx, col, claves[cerca[0]], "parece un error de tipeo")

        # categoría / subcategoría / marca vacías que el nombre delata
        if nombre_n:
            if "categoria" in df.columns and _vacio(df.at[idx, "categoria"]):
                for patron, clave in PISTAS_CATEGORIA:
                    if re.search(patron, nombre_n) and clave in vocab["categoria"]:
                        corregir(sku, idx, "categoria", vocab["categoria"][clave], "deducido del nombre")
                        break
            if "subcategoria" in df.columns and _vacio(df.at[idx, "subcategoria"]):
                for patron, clave in PISTAS_SUBCATEGORIA:
                    if re.search(patron, nombre_n) and clave in vocab["subcategoria"]:
                        corregir(sku, idx, "subcategoria", vocab["subcategoria"][clave], "deducido del nombre")
                        break
            if "marca" in df.columns and _vacio(df.at[idx, "marca"]):
                candidatas = [k for k in vocab["marca"] if k and re.search(rf"\b{re.escape(k)}\b", nombre_n)]
                if candidatas:
                    corregir(sku, idx, "marca", vocab["marca"][max(candidatas, key=len)], "la marca aparece en el nombre")

        # rollos / metros / unidades
        cat_n = _n(df.at[idx, "categoria"]) if "categoria" in df.columns and not _vacio(df.at[idx, "categoria"]) else ""
        por_unidad = cat_n in CATEGORIAS_POR_UNIDAD
        if nombre_n and not por_unidad and "rollos" in df.columns and "metros_rollo" in df.columns:
            r_nom, m_nom = parsear_rollos_metros(nombre)
            if r_nom is not None:
                if _vacio(df.at[idx, "rollos"]):
                    corregir(sku, idx, "rollos", r_nom, "sacado del nombre")
                if _vacio(df.at[idx, "metros_rollo"]):
                    corregir(sku, idx, "metros_rollo", m_nom, "sacado del nombre")
                r_col = pd.to_numeric(df.at[idx, "rollos"], errors="coerce")
                m_col = pd.to_numeric(df.at[idx, "metros_rollo"], errors="coerce")
                if pd.notna(r_col) and pd.notna(m_col) and (r_col != r_nom or abs(m_col - m_nom) > 0.01):
                    avisos.append(
                        f"{sku}: el nombre dice {r_nom} rollo(s) de {m_nom:g} m pero las columnas dicen "
                        f"{r_col:g} de {m_col:g} m — revisar cuál es el correcto (no se tocó)"
                    )
        if not por_unidad and "metros_totales" in df.columns:
            r = pd.to_numeric(df.at[idx, "rollos"], errors="coerce") if "rollos" in df.columns else None
            m = pd.to_numeric(df.at[idx, "metros_rollo"], errors="coerce") if "metros_rollo" in df.columns else None
            if r is not None and m is not None and pd.notna(r) and pd.notna(m):
                esperado = float(r * m)
                actual = pd.to_numeric(df.at[idx, "metros_totales"], errors="coerce")
                if pd.isna(actual):
                    corregir(sku, idx, "metros_totales", esperado, f"{r:g} rollos x {m:g} m")
                elif abs(actual - esperado) > 0.5:
                    corregir(sku, idx, "metros_totales", esperado, f"no calzaba con {r:g} rollos x {m:g} m")
        if cat_n == "toallitas humedas" and "unidades" in df.columns and _vacio(df.at[idx, "unidades"]) and nombre_n:
            m_pack = re.search(r"(\d+)\s*x\s*(\d+)\s*(?:un|uni|unid|unidades)?\b", nombre_n)
            m_uni = re.search(r"(\d+)\s*(?:un|uni|unid|unidades)\b", nombre_n)
            total = int(m_pack.group(1)) * int(m_pack.group(2)) if m_pack else (int(m_uni.group(1)) if m_uni else None)
            if total:
                corregir(sku, idx, "unidades", total, "total de toallitas deducido del nombre")

    # nombre estándar / grupo: un grupo = un nombre
    if {"grupo_id", "nombre_estandar"} <= set(df.columns):
        df["_g"] = df["grupo_id"].map(lambda g: None if _vacio(g) else _normalizar_grupo(g))
        for g, sub in df[df["_g"].notna()].groupby("_g"):
            nombres = Counter(str(v).strip() for v in sub["nombre_estandar"] if not _vacio(v))
            if not nombres:
                continue
            # misma frase con otra escritura -> se junta bajo la más usada
            por_clave = {}
            for nom, c in nombres.items():
                por_clave.setdefault(_n(nom), Counter())[nom] += c
            ganadora_por_clave = {k: c.most_common(1)[0][0] for k, c in por_clave.items()}
            if len(ganadora_por_clave) == 1:
                oficial = next(iter(ganadora_por_clave.values()))
            else:
                oficial = nombres.most_common(1)[0][0]
                avisos.append(
                    f"Grupo {g}: tiene nombres estándar distintos ({', '.join(sorted(nombres))}); "
                    f"se usó '{oficial}' (el más repetido) — confirmar que sea el correcto"
                )
            for i in sub.index:
                actual = df.at[i, "nombre_estandar"]
                if _vacio(actual) or str(actual).strip() != oficial:
                    corregir(str(df.at[i, "sku_interno"]).strip(), i, "nombre_estandar", oficial, f"unificado con el resto del grupo {g}")
        # nombre sin grupo: si ese nombre pertenece a un único grupo, se completa
        grupos_de_nombre = {}
        for _, f in df[df["_g"].notna()].iterrows():
            if not _vacio(f["nombre_estandar"]):
                grupos_de_nombre.setdefault(_n(f["nombre_estandar"]), set()).add(f["_g"])
        for i in df.index[df["_g"].isna()]:
            nom = df.at[i, "nombre_estandar"]
            if not _vacio(nom) and len(grupos_de_nombre.get(_n(nom), ())) == 1:
                g = next(iter(grupos_de_nombre[_n(nom)]))
                corregir(str(df.at[i, "sku_interno"]).strip(), i, "grupo_id", g, "ese nombre estándar ya pertenece a ese grupo")
        df = df.drop(columns="_g")

    return df, correcciones, avisos
