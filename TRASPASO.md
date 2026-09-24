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
| Planilla del equipo | **Google Sheets** (cuenta `monitor.de.precios1@gmail.com`) | Pestaña **Arreglar Link**: reemplazar un link roto. Pestaña **Catálogo**: el catálogo COMPLETO, una fila por producto — agregar una fila es un alta, borrarla es una baja, cambiar un valor es una edición. Todo sin tocar código ni GitHub. |

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
4. En la pestaña **Arreglar Link** de la planilla, agregá una fila:

   | Código | Link nuevo | Nota |
   |---|---|---|
   | `TC-034` | `https://www.sitio.cl/producto-nuevo` | Cambió el link 09/2026 |

5. Listo. En la próxima corrida automática (máximo ~8 horas) el precio vuelve
   solo. El producto va a aparecer con un ✏️ al lado del nombre.

**Reglas de la planilla:**

- No cambiar los títulos de las columnas (**Código**, **Link nuevo**, **Nota**); el orden sí puede cambiar.
- Un **Código** que no exista en el catálogo simplemente se ignora, no rompe nada.
- Para deshacer una corrección, borrá la fila.
- Si la planilla se cae o se despublica, el scraper sigue funcionando con la
  última copia buena (`url_overrides_cache.json` en el repo).

### Si hay que recrear la planilla

Una Google Sheet en la cuenta `monitor.de.precios1@gmail.com` con **dos pestañas**:

- **Arreglar Link** — columnas exactas (en este texto): `Código`, `Link nuevo`, `Nota`.
- **Catálogo** — el catálogo COMPLETO, una fila por producto, columnas exactas:
  `Código`, `Producto`, `Marca`, `Retailer`, `Link`, `Categoría`, `Subcategoría`,
  `Rollos`, `Metros por rollo`, `Metros totales`, `Unidades`, `Grupo`,
  `Nombre estándar` (estas dos últimas opcionales). El orden de las columnas no
  importa (el código las busca por nombre), pero los nombres tienen que ser
  exactos: si se cambia alguno hay que actualizar el diccionario
  `ENCABEZADOS_PRODUCTOS` / `ENCABEZADOS_URL_FIXES` en `scraper.py` y `app.py`
  para que coincida. Para arrancarla, exportar `productos.csv` como CSV y
  pegarlo tal cual en esta pestaña (con los encabezados en español de arriba).

Para cada pestaña: *Archivo → Compartir → Publicar en la Web*, elegir **esa hoja**
(no "todo el documento") y formato **CSV** → Publicar. Cada una da un link propio
que termina en `output=csv`. Pegarlos en las constantes de `scraper.py` **y**
`app.py`:

| Constante | De qué pestaña |
|---|---|
| `OVERRIDES_CSV_URL` | Arreglar Link |
| `CATALOGO_CSV_URL` | Catálogo (vacío = función apagada) |

Y en `app.py`: `PLANILLA_EDIT_URL` = el link normal de edición (termina en `/edit`).

Compartir la planilla (botón *Compartir*) con quien la va a mantener, como
**Editor**. No usar "cualquiera con el enlace puede editar".

## Tarea de rutina #2 — Agregar, editar o sacar un producto

Todo se hace en la pestaña **Catálogo** de la planilla, **sin programar y sin
entrar a GitHub**. Esa pestaña ES el catálogo completo — no hay columna
Acción, cada fila es directamente un producto:

- **Agregar** un producto: sumá una fila nueva abajo de todo.
- **Editar** un producto: cambiá el valor que haga falta en su fila (ej. Marca
  o Link).
- **Sacar** un producto: borrá su fila entera.

Columnas: `Código, Producto, Marca, Retailer, Link, Categoría, Subcategoría,
Rollos, Metros por rollo, Metros totales, Unidades` (más `Grupo` / `Nombre
estándar`, opcionales, para cruzar el mismo producto entre retailers).

- **Código**: libre, que no se repita (seguir la serie `TC-###`).
- **Retailer**: la **clave exacta** en minúscula — `jumbo`, `santaisabel`, `tottus`,
  `unimarc`, `alvi`, `acuenta`, `centralmayorista`, `liquimax`, `imanweb`, `dimak` (no el nombre "bonito").
- **Categoría** y **Subcategoría**: igual que en `ovella.csv` (o los valores ya usados)
  para que el dashboard agrupe bien.
- **Papel higiénico / toalla:** **Metros totales**, **Rollos** (cuántos rollos trae el
  pack) y **Metros por rollo** (metros de cada rollo; Metros totales = Rollos ×
  Metros por rollo). **Subcategoría** = `Doble Hoja` / `Hoja Simple` / `Triple Hoja`.
  Se comparan en $/metro.
- **Servilletas:** dejar **Metros totales**/**Rollos**/**Metros por rollo** vacíos y llenar
  **Unidades** (cantidad del pack). **Subcategoría** = `Cocktail` / `Mesa`. Se
  comparan en $/unidad.
- **Formato mayorista por manga/caja** (ej. Central Mayorista publica el precio
  de 12 packs juntos): llenar **Rollos**/**Metros por rollo**/**Metros totales** con los
  datos de **un pack suelto** y poner en **Unidades** cuántos packs trae la manga
  (ej. `12` para "MANGAx12"). La app calcula *Precio pack* = precio ÷ 12 y con
  eso el $/metro, así compite parejo contra un pack suelto de otro súper.

En la próxima corrida automática (máx. ~8 h) el scraper reemplaza el catálogo
(`productos.csv`) por lo que haya en la planilla y lo sube solo con el mismo
commit que sube los precios (usa el token automático de GitHub Actions, no una
cuenta ni contraseña de nadie, así que no hay nada que se pueda vencer). Las
altas aparecen mientras tanto en el dashboard marcadas con 🆕 (provisorio);
ediciones y bajas se ven recién cuando corre el scraper.

**Qué pasa si alguien se equivoca en la planilla** (esto es lo que evita que
un error tumbe la app):

- **Error puntual en una fila** (falta el Link, el Retailer está mal
  escrito, un campo numérico tiene letras, falta Producto/Marca/Categoría/
  Subcategoría, un Código repetido): esa fila sola se ignora — no se aplica —
  y aparece avisada en el panel **➕ Agregar, editar o sacar un producto** del
  dashboard (se puede revisar sin esperar la corrida) y también en **Salud
  del scraper** después de que corra. El resto del catálogo se actualiza
  normal.
- **Error grave** (se borró sin querer un montón de filas de golpe, se movieron
  las columnas, se pegó la planilla a medias, quedó vacía): si el catálogo
  resultante tiene **más de 30% menos productos** que el actual, el scraper
  **no aplica nada** — sigue con el último catálogo bueno — y lo avisa fuerte
  en el dashboard (panel lateral y "Salud del scraper"). Para corregirlo,
  basta con arreglar la planilla; no hace falta avisarle a nadie con acceso a
  GitHub.
- Si la planilla se cae o se despublica, el scraper sigue con la última copia
  buena guardada en el repo (`catalogo_cache.csv`).

Si en algún momento hace falta, también se puede editar `productos.csv`
directo en GitHub (ícono de lápiz en la página del archivo), pero ya no es
necesario para el uso normal.

## Respaldos — cómo volver atrás si algo se rompió

Hay dos respaldos independientes, uno para la planilla y otro para el catálogo
ya aplicado:

- **La planilla de Google** tiene su propio historial de versiones, automático
  y gratis, sin que nadie tenga que configurar nada: *Archivo → Ver historial
  de versiones* (o *Ver historial de versiones → Ver historial de versiones*
  según el menú). Ahí se puede ver quién cambió qué y cuándo, y **restaurar la
  planilla completa a un momento anterior** con un clic. Si alguien borra la
  planilla entera por error, Drive la guarda en la Papelera 30 días.
- **El catálogo ya aplicado** (`productos.csv`) se respalda solo de dos formas:
  - Cada corrida del scraper guarda una copia fechada en la carpeta
    `respaldos/` del repo (`respaldos/productos_2026-S39.csv`, una por semana
    calendario, se puede abrir y descargar directo desde GitHub sin saber
    programar). Se conserva como máximo 1 año de copias.
  - Además, **todo el historial completo queda en git** — cada commit del bot
    (`🤖 Actualización automática de precios`) es un punto de restauración.
    Para volver a una versión puntual hace falta alguien con acceso a GitHub
    (pestaña **History** del archivo `productos.csv` en GitHub → elegir una
    versión vieja → "..." → "View file" → copiar y pegar su contenido sobre
    `productos.csv` actual, commitear).

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

## Historial: Knasta (Líder) — sacado el 04/09/2026

Líder (`super.lider.cl`) tiene protección anti-bot activa (desafío "Robot or
human?") que nunca se pudo pasar. Como workaround se usó **Knasta**, un
comparador de precios independiente que mostraba los mismos precios de Líder
sin ese bloqueo. Knasta funcionó un tiempo y después empezó a devolver `403`
a **todo** el rango de IP de GitHub Actions (confirmado: es bloqueo por rango
de IP de datacenter, no por horario). Se probaron headers de navegador,
reintentos con backoff, un proxy público y mover el horario del cron — nada
pasó el bloqueo. Se sacaron sus 22 SKU de `productos.csv` y el código
específico de `scraper.py`/`retailers.yaml`.

**Si en el futuro alguien quiere retomar Líder o Knasta:** la única vía que
quedaría es un servicio de scraping pago con proxies residenciales
(ScraperAPI, ZenRows o similar) — con el volumen de este proyecto un tier
gratis o barato probablemente alcanza. El código viejo (función
`_consultar_knasta`, GraphQL de Líder) está en el historial de git de este
repo si sirve de punto de partida.

## Historial: La Oferta (laoferta.cl) — no incorporado 09/2026

`laoferta.cl` (mayorista, precio por lote) tiene el mismo tipo de bloqueo
que Líder: challenge de Cloudflare en todo el sitio. Se probó `curl_cffi`,
`cloudscraper`, la API de WooCommerce, lectores proxy y Playwright headless —
nada pasa. **Sí** se logró pasar con un Chrome real automatizado con
anti-detección (`patchright`) pero **solo con ventana visible** (headed), lo
que en GitHub Actions obliga a correr Chrome bajo un display virtual (Xvfb).
Se decidió no implementarlo por ahora: es frágil (Cloudflare puede endurecer
el challenge para las IP de datacenter de Actions, igual que con Knasta) y
nadie sin conocimientos técnicos podría mantenerlo. Queda pendiente para
evaluar junto con Líder si algún día se paga un servicio de scraping.
