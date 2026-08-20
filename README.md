# Monitor de Pricing — Dipisa / Ovella

App Streamlit que consulta en vivo los precios de la competencia y calcula
comparativas ($/metro, piso de categoría, etc.) sin necesidad de tomarlos
manualmente. No depende de ninguna IA ni API paga: todo el cálculo es
aritmética simple sobre precios leídos directo del sitio del retailer.

## Uso rápido (local)

```bash
pip install -r requirements.txt
playwright install chromium   # solo necesario si usarás retailers con metodo: playwright
streamlit run app.py
```

## Cómo agregar productos de la competencia

Agrega una fila en `productos.csv` con
`sku_interno,producto,marca,metros_totales,retailer,url` y sube el cambio al
repo (push a GitHub). No requiere tocar código. El dashboard agrupa
automáticamente cualquier producto que comparta `metros_totales` con un SKU
de `ovella.csv` — si no comparte metraje con ninguno, aparece igual en la
sección "Otros productos monitoreados" al final de la página.

## Cómo agregar un SKU de Ovella

Agrega una fila en `ovella.csv` con `sku_ovella,producto,metros_totales`
(metraje total del paquete: rollos × metros por rollo). Se convierte
automáticamente en una tarjeta nueva del dashboard, con sus competidores de
igual metraje debajo.

## Cómo agregar una tienda nueva

Edita `retailers.yaml` — cada tienda define un `metodo`:

- **meta_tag**: rápido, sin navegador. Funciona en tiendas que traen el precio
  en el HTML crudo (ej. tiendas VTEX como Santa Isabel, Jumbo — buscar
  `product:price:amount` en "Ver código fuente" de una página de producto).
  Ojo: el atributo puede ser `property=` o `name=` según el sitio.
- **text_pattern**: busca el precio como texto plano en el HTML crudo (sin
  meta tags), útil en sitios con SSR real (Next.js) que ya renderizan el
  precio desde el servidor.
- **playwright**: renderiza con navegador headless y lee el precio con un
  selector CSS. Necesario cuando el precio no está en el HTML crudo. No
  garantiza pasar protecciones anti-bot activas (ver nota sobre Líder abajo).
  Frágil si el sitio usa clases CSS autogeneradas que cambian en cada deploy.
- **playwright_text**: como `playwright`, pero busca el precio con un patrón
  de texto sobre el contenido ya renderizado en vez de un selector CSS — más
  robusto contra sitios que regeneran sus clases CSS en cada deploy (ej.
  Alvi, con Next.js/CSS modules).

El archivo `retailers.yaml` trae comentarios con el paso a paso para agregar
un retailer nuevo.

**Importante**: nunca apuntar directo a endpoints tipo `/api/...` de un
retailer sin revisar antes su `robots.txt` — varios sitios los bloquean
explícitamente para bots aunque respondan igual si se les consulta.

### Retailers con protección anti-bot

Líder (`super.lider.cl`) devuelve una página de verificación
("Robot or human?") en vez del HTML del producto ante un request simple —
no es un tema de headers, es bot-detection activa. Está deshabilitado en
`retailers.yaml` hasta evaluar una fuente de datos alternativa. Unimarc
presenta un problema similar y todavía no está configurado. Alvi (Akamai) y
Tottus (desafío JS de Cloudflare) también bloquean requests simples, pero un
navegador headless sí pasa en ambos — están configurados con
`metodo: playwright_text`.

## Despliegue (para que el equipo la use sin depender de una persona)

**Streamlit Community Cloud** (gratis, conectado al repo de GitHub) es la
opción más simple. Consideraciones:

- Si usas algún retailer con `metodo: playwright`, agrega un archivo
  `packages.txt` en la raíz del repo con las dependencias del sistema que
  necesita Chromium headless (Streamlit Cloud lo instala automáticamente si
  detecta ese archivo). Sin esto, Playwright puede fallar en la nube aunque
  funcione en local.
- Los retailers con `metodo: meta_tag` o `text_pattern` no tienen este
  problema — corren en cualquier lado sin dependencias extra.
- El caché de resultados dura 30 minutos (`ttl=1800`) para no golpear los
  sitios en cada refresh de pantalla; el botón "Forzar Recarga" lo limpia
  manualmente. El caché se invalida solo si cambia el contenido de
  `productos.csv` (vía hash) o al pasar el TTL.

## Estructura

```
app.py             # interfaz + orquestación
ovella.csv          # SKU base de Ovella (ancla de cada tarjeta del dashboard)
productos.csv       # qué productos de la competencia monitorear y en qué tiendas
retailers.yaml       # cómo extraer el precio en cada tienda
requirements.txt
packages.txt         # dependencias de sistema para Playwright en la nube
```
