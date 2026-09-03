# Traspaso — Monitor de Precios Dipisa

Documento para quien quede a cargo del monitor. Explica qué cuentas existen,
dónde está cada cosa y cómo hacer el mantenimiento de rutina **sin programar**.

## Qué es esto

Una app ([Streamlit](https://streamlit.io)) que 3 veces al día consulta los
precios de la competencia en los sitios de los supermercados y los muestra
comparados contra los precios de Ovella. No usa ninguna IA ni servicio pago.

Piezas:

| Pieza | Dónde vive | Qué hace |
|---|---|---|
| `scraper.py` | Se ejecuta solo en **GitHub Actions** (`.github/workflows/scraper.yml`) | Baja los precios y los guarda en el repo |
| `app.py` | **Streamlit Community Cloud** | Muestra el dashboard |
| `productos.csv` | Repo de GitHub | Lista de productos de la competencia a monitorear |
| `ovella.csv` | Repo de GitHub | Productos de Ovella (referencia de cada comparación) |
| Planilla del equipo | **Google Sheets** (cuenta `monitor.de.precios1@gmail.com`) | Pestaña `url_fixes`: reemplazar un link roto. Pestaña `productos_nuevos`: sumar un SKU. Todo sin tocar código. |

## Cuentas y accesos

> Completar con los datos reales y guardar las contraseñas en el gestor de
> contraseñas de la empresa (o donde corresponda). **No dejar contraseñas en
> este archivo si el repo es público.**

- **Correo del monitor:** `monitor.de.precios1@gmail.com` — es la cuenta "de servicio".
  Es dueña de la planilla de Google. Sirve de recuperación para todo lo demás.
- **GitHub:** el repositorio está en la cuenta `_______`. Para editar `productos.csv`
  hace falta ser colaborador.
- **Streamlit Community Cloud:** la app está conectada al repo de GitHub y se
  redespliega sola con cada cambio. Login con `_______`.

## Tarea de rutina #1 — Un link de producto dejó de actualizarse

Síntoma: en el dashboard, un producto muestra el estado **"⚠️ Últ. precio (fecha vieja)"**
y esa fecha no cambia con los días. Casi siempre es porque el supermercado
cambió la dirección (URL) de ese producto en su sitio.

**Cómo se arregla (sin programar):**

1. En el dashboard, abrí el panel **"🔗 ¿Un link de producto está roto o cambió?"**
   y hacé clic en **"Abrir la planilla de correcciones"**.
2. Anotá el **código** del producto que falla (aparece como `TC-###`; también
   sale en el panel "Salud del scraper").
3. Buscá el producto en el sitio del supermercado y copiá la URL nueva de la
   barra de direcciones.
4. En la planilla, agregá una fila:

   | sku_interno | url_nuevo | nota |
   |---|---|---|
   | `TC-034` | `https://www.sitio.cl/producto-nuevo` | Cambió el link 09/2026 |

5. Listo. En la próxima corrida automática (máximo ~8 horas) el precio vuelve
   solo. El producto va a aparecer con un ✏️ al lado del nombre.

**Reglas de la planilla:**

- No cambiar los títulos de las columnas ni el orden (`sku_interno`, `url_nuevo`, `nota`).
- Un `sku_interno` que no exista en `productos.csv` simplemente se ignora, no rompe nada.
- Para deshacer una corrección, borrá la fila.
- Si la planilla se cae o se despublica, el scraper sigue funcionando con la
  última copia buena (`url_overrides_cache.json` en el repo).

### Si hay que recrear la planilla

Una Google Sheet en la cuenta `monitor.de.precios1@gmail.com` con **dos pestañas**:

- `url_fixes` — columnas exactas: `sku_interno`, `url_nuevo`, `nota`.
- `productos_nuevos` — columnas exactas: `sku_interno`, `producto`, `marca`,
  `metros_totales`, `retailer`, `url`, `categoria`, `subcategoria`, `rollos`,
  `metros_rollo`.

Para cada pestaña: *Archivo → Compartir → Publicar en la Web*, elegir **esa hoja**
(no "todo el documento") y formato **CSV** → Publicar. Cada una da un link propio
que termina en `output=csv`. Pegarlos en las constantes de `scraper.py` **y**
`app.py`:

| Constante | De qué pestaña |
|---|---|
| `OVERRIDES_CSV_URL` | `url_fixes` |
| `PRODUCTOS_NUEVOS_CSV_URL` | `productos_nuevos` (vacío = función apagada) |

Y en `app.py`: `PLANILLA_EDIT_URL` = el link normal de edición (termina en `/edit`).

Compartir la planilla (botón *Compartir*) con quien la va a mantener, como
**Editor**. No usar "cualquiera con el enlace puede editar".

## Tarea de rutina #2 — Agregar un producto nuevo a monitorear

Columnas de una fila de producto: `sku_interno,producto,marca,metros_totales,retailer,url,categoria,subcategoria,rollos,metros_rollo`.

- `sku_interno`: código libre que no se repita (seguir la serie `TC-###`).
- `retailer`: la **clave exacta** en minúscula — `jumbo`, `santaisabel`, `tottus`,
  `unimarc`, `alvi`, `acuenta` (no el nombre "bonito").
- `categoria` y `subcategoria`: igual que en `ovella.csv` (ej. `Papel Higienico` /
  `Doble Hoja`) para que el dashboard agrupe bien.
- `rollos` = cuántos rollos trae el pack; `metros_rollo` = metros de cada rollo
  (`metros_totales` = `rollos` × `metros_rollo`).

**Opción A — desde la planilla (sin programar):** en la pestaña `productos_nuevos`
agregá una fila con esas columnas. En la próxima corrida (~8 h) aparece en el
dashboard marcado con 🆕 (provisorio). **Cada tanto** (tarea de ~5 min) alguien
con acceso a GitHub pasa esas filas a `productos.csv` y las borra de la pestaña,
para que la lista maestra quede completa.

**Opción B — directo en GitHub:** editar `productos.csv` (ícono de lápiz en la
página del archivo) y agregar la fila ahí.

## Correr el scraper a mano

En GitHub: pestaña **Actions → "Actualizador de Precios Dipisa" → Run workflow**.
Tarda unos minutos y al terminar sube los precios nuevos solo.

## Si algo se rompe

- **El dashboard no carga / muestra error:** revisar en GitHub → Actions si la
  última corrida falló (marca roja). El panel "Salud del scraper" del dashboard
  también muestra qué SKU fallaron en la última corrida.
- **Un supermercado entero deja de traer precios:** probablemente cambió su
  sitio o agregó bloqueo anti-bot. Eso sí necesita a alguien que sepa tocar
  `scraper.py` / `retailers.yaml`.
