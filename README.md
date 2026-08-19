# Monitor de Pricing — Dipisa / Teachu

App Streamlit que consulta en vivo los precios de la competencia y calcula
comparativas ($/metro, piso de categoría, etc.) sin necesidad de tomarlos
manualmente.

## Uso rápido (local)

```bash
pip install -r requirements.txt
playwright install chromium   # solo necesario si usarás retailers con metodo: playwright
streamlit run app.py
```

## Cómo agregar productos

Edita `config/productos.csv` y agrega una fila con:
`sku_interno,producto,marca,metros_totales,retailer,url`

No requiere tocar código.

## Cómo agregar una tienda nueva

Edita `config/retailers.yaml` — cada tienda define un `metodo`:

- **meta_tag**: rápido, sin navegador. Funciona en tiendas que traen el precio
  en el HTML crudo (ej. tiendas VTEX como Santa Isabel, Jumbo, Paris — buscar
  `product:price:amount` en "Ver código fuente" de una página de producto).
- **playwright**: renderiza con navegador headless. Necesario cuando el precio
  no está en el HTML crudo, o cuando se necesita precio normal Y de oferta por
  separado.

El archivo `config/retailers.yaml` trae comentarios con el paso a paso e
ejemplos comentados para agregar un retailer con Playwright.

**Importante**: nunca apuntar directo a endpoints tipo `/api/...` de un
retailer sin revisar antes su `robots.txt` — varios sitios los bloquean
explícitamente para bots aunque respondan igual si se les consulta.

## Despliegue (para que el equipo la use sin depender de una persona)

**Streamlit Community Cloud** (gratis, conectado al repo de GitHub) es la
opción más simple. Consideraciones:

- Si usas algún retailer con `metodo: playwright`, agrega un archivo
  `packages.txt` en la raíz del repo con las dependencias del sistema que
  necesita Chromium headless (Streamlit Cloud lo instala automáticamente si
  detecta ese archivo). Sin esto, Playwright puede fallar en la nube aunque
  funcione en local.
- Los retailers con `metodo: meta_tag` no tienen este problema — corren en
  cualquier lado sin dependencias extra.
- El caché de resultados dura 30 minutos (`ttl=1800`) para no golpear los
  sitios en cada refresh de pantalla; el botón "Forzar Recarga" lo limpia
  manualmente.

## Estructura

```
app.py                  # interfaz + orquestación
config/productos.csv    # qué productos monitorear y en qué tiendas
config/retailers.yaml   # cómo extraer el precio en cada tienda
scrapers/base_scraper.py# motor Playwright genérico (referencia/reutilizable)
```
