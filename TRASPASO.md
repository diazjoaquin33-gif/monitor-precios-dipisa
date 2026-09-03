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
| Planilla de correcciones de URL | **Google Sheets** (cuenta `moitor.de.precios1@gmail.com`) | Permite reemplazar un link roto sin tocar código |

## Cuentas y accesos

> Completar con los datos reales y guardar las contraseñas en el gestor de
> contraseñas de la empresa (o donde corresponda). **No dejar contraseñas en
> este archivo si el repo es público.**

- **Correo del monitor:** `moitor.de.precios1@gmail.com` — es la cuenta "de servicio".
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

Tiene que ser una Google Sheet en la cuenta `moitor.de.precios1@gmail.com` con una
hoja cuyas columnas sean exactamente `sku_interno`, `url_nuevo`, `nota`. Después:

1. *Archivo → Compartir → Publicar en la Web → "Valores separados por comas (.csv)"* → Publicar.
2. Copiar ese link (termina en `output=csv`) y reemplazarlo en:
   - `scraper.py` → constante `OVERRIDES_CSV_URL`
   - `app.py` → constante `OVERRIDES_CSV_URL`
3. Copiar el link normal de edición (termina en `/edit`) y ponerlo en
   `app.py` → constante `PLANILLA_EDIT_URL`.
4. Compartir la planilla (botón *Compartir*) con las personas que la van a
   mantener, como **Editor**. No usar "cualquiera con el enlace puede editar".

## Tarea de rutina #2 — Agregar un producto nuevo a monitorear

Editar `productos.csv` en GitHub (ícono de lápiz en la página del archivo) y
agregar una fila con: `sku_interno,producto,marca,metros_totales,retailer,url,categoria,subcategoria`.
El `sku_interno` es un código libre que no se repita (seguir la serie `TC-###`).
`categoria` y `subcategoria` deben escribirse igual que en `ovella.csv` para que
el dashboard agrupe bien.

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
