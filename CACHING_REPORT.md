# Optimización de rendimiento: Caching

Este informe documenta las decisiones de caching aplicadas sobre el monorepo de Nexova (`uis/backoffice` + `services/api`) en la rama `feature/caching-optimisation`. Parte de un estado sin ningún mecanismo de caching, lazy loading ni memoización previo — confirmado por búsqueda exhaustiva antes de empezar.

## 1. Decisiones en el frontend (`uis/backoffice`)

### Lazy Loading (`next/dynamic`)

**Bloque de resultados de incidencias** — [`app/(incidents)/incidents/page.tsx`](uis/backoffice/app/(incidents)/incidents/page.tsx)
`SummaryHeader`, `InvalidBreakdown`, `SatisfactionIndex`, `CategoryBreakdown`, `StatusBreakdown` y `ExportButton` solo se renderizan cuando `summary` tiene valor, es decir, **después** de que el usuario suba un CSV. Nada de ese código es necesario en la carga inicial de la ruta, así que se difirió con `next/dynamic`. Verificado con `next build`: cada componente genera su propio chunk independiente del bundle de la página (ej. `uis_backoffice_components_incidents_CategoryBreakdown_tsx_*.js`), confirmando que ese JS ya no viaja con la carga inicial de `/incidents`.

**Tablas de inventario** — [`components/inventory/ProductsTable.tsx`](uis/backoffice/components/inventory/ProductsTable.tsx) y [`components/inventory/OrdersHistoryTable.tsx`](uis/backoffice/components/inventory/OrdersHistoryTable.tsx)
Ambas se usan desde sus páginas (`inventory/products/page.tsx`, `inventory/orders/page.tsx`) recién después de resolver el `useEffect` + fetch inicial (`isLoading` guard) — nunca en el primer paint. Se difirieron con `next/dynamic` para reducir el JS necesario en el primer render de esas rutas. Esto es relevante porque `REPORT.md` (auditoría de rendimiento previa) dejó pendiente un LCP móvil de ~2.9s en producción, por encima del umbral de 2.5s, como único hallazgo de rendimiento frontend sin resolver — hoy no había ningún code-splitting a nivel de componente en `uis/backoffice`, solo el que aporta el App Router por ruta.

Se descartó como candidato `OutboundOrderForm`/`InboundOrderForm`: viven en rutas propias (`/inventory/orders/inbound`, `/outbound`), así que Next ya las separa en su propio chunk vía code-splitting de rutas — un `next/dynamic` ahí no habría aportado nada nuevo.

### `useMemo`

[`components/inventory/OrdersHistoryTable.tsx`](uis/backoffice/components/inventory/OrdersHistoryTable.tsx):

```ts
const sorted = useMemo(
  () =>
    [...orders].sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    ),
  [orders]
);
```

Antes, `sorted` se recalculaba en **cada render** del componente (crear un array nuevo, parsear fechas con `new Date()` y ordenar), incluso en re-renders del padre que no cambiaban `orders`. No es un cálculo trivial — crece linealmente con el histórico de órdenes, que es justamente el dato que se espera que aumente con el uso real de la app. El beneficio es mayor a medida que el volumen de órdenes crece (ver seed de datos en la sección de backend).

## 2. Decisiones en el backend (`services/api`)

Se implementó una caché en memoria con TTL (`services/api/cache.py`), sin dependencias nuevas: un diccionario `{clave: (expira_en, valor)}` con `get_or_set()` e `invalidate(prefix)`. Vive en el proceso — se pierde al reiniciar y no se comparte entre workers/réplicas; trade-off aceptado dado el volumen y despliegue actuales del proyecto.

| Endpoint | Coste de la operación | Frecuencia estimada | TTL | Invalidación |
|---|---|---|---|---|
| `GET /inventory/products` | Recalcula `_stock_by_asset`: 2 queries `GROUP BY` sobre todo el histórico de `AssetEntry`/`AssetExit` en cada request | Alta — es la vista principal de inventario, se recarga en cada visita a `/inventory/products` | 30s | Al crear un producto o registrar una entrada/salida (`create_product`, `create_inbound_order`, `create_outbound_order`) |
| `GET /inventory/orders` | 2 `SELECT *` completos (entradas y salidas) + join en memoria + sort; crece linealmente con el histórico | Alta — vista de solo lectura, recargada en cada visita | 30s | Al registrar una entrada o salida (`create_inbound_order`, `create_outbound_order`) |
| `GET /suppliers` | Lee el JSON completo de TinyDB y filtra en Python (por `country`/`category`) en cada request | Media — catálogo consultado con menos frecuencia que inventario, pero sin filtrado en base de datos | 120s | Al crear, actualizar (tarifa o estado) o eliminar un proveedor |

Detalle importante de `/suppliers`: como el endpoint acepta `country` y `category` como query params, la clave de caché incluye ambos (`suppliers:{country}:{category}`) — cada combinación de filtros es una entrada distinta. La invalidación limpia **todo** el prefijo `suppliers:`, no una clave puntual, porque una escritura afecta a todas las combinaciones de filtro posibles.

**Verificación funcional realizada** (con `TestClient` de FastAPI contra un SQLite temporal, sin tocar los datos reales de TinyDB):
- `GET /inventory/products`: primera llamada 27.5ms; llamadas repetidas dentro del TTL, 1.7ms y 0.9ms.
- Tras un `POST /inventory/orders/inbound`, la siguiente llamada a `GET /inventory/products` reflejó el nuevo stock de inmediato (invalidación correcta, sin servir el valor cacheado obsoleto).
- `GET /suppliers` antes y después de un `POST /suppliers`: el conteo de proveedores se actualizó en la siguiente lectura sin esperar el TTL.

Con el volumen actual de datos (6 assets, 4 entradas, 3 salidas, sembrados por `services/api/seed_inventory.py`) las diferencias absolutas de latencia son pequeñas porque las agregaciones corren sobre pocas filas; el ahorro relativo (30ms → <2ms) ya es significativo y crecerá en proporción al volumen conforme el histórico de movimientos aumente, que es exactamente el patrón que noTTL agrava.

**Explícitamente no cacheado:**
- `POST/PATCH/DELETE` en cualquier router — son escrituras.
- `POST /auth/login`, `GET /auth/me`, `GET /profiles/me` — datos de sesión/usuario autenticado; una clave de caché compartida aquí sería una fuga de datos entre usuarios.
- `POST /api/incidents/analyze`, `GET /api/incidents/results/export` — el input (CSV subido) y el resultado dependen del último análisis en memoria de cada proceso; no son repetibles entre peticiones.
- `GET /users` — la auditoría de serialización previa (`docs/serialization-audit.md`) dejó pendiente un tema de autorización sobre este endpoint; cachearlo ahora reforzaría ese problema en lugar de resolverlo. Se deja fuera de alcance de forma consciente.
- `GET /users/{id}`, `GET /suppliers/{id}`, `GET /inventory/products/{id}` — lookups puntuales de bajo costo y baja frecuencia; la complejidad de una clave de caché por id no se justifica.
- `get_current_user` (`security.py`) — es el hot path de autenticación de toda la API. Cachear ahí introduce riesgo (un cambio de contraseña o una baja de usuario tardarían en reflejarse) para un beneficio menor frente a los tres endpoints ya cubiertos. Decisión consciente de mantenerlo fuera de este alcance.

## 3. Intercambios reconocidos (frescura vs. rendimiento)

**Stock y órdenes de inventario (TTL 30s):** son datos operativos internos, no financieros ni de cara al cliente. Un desfase de hasta 30 segundos entre un movimiento de stock y que se refleje en `/inventory/products` es aceptable: nadie tramita entradas/salidas con una cadencia de segundos, y la propia UI no fuerza refrescos automáticos. A cambio, se evita recalcular agregaciones sobre todo el histórico en cada visita a estas pantallas. Un TTL más corto (ej. 5s) apenas reduciría el riesgo de desfase percibido pero cancelaría gran parte del beneficio en ráfagas de lecturas (varios usuarios mirando el mismo listado en la misma ventana).

**Proveedores (TTL 120s):** los datos de proveedores (tarifa, estado, renovación de contrato) cambian con la cadencia de decisiones administrativas, no de operación diaria — es razonable que un cambio de tarifa tarde hasta 2 minutos en propagarse a todas las lecturas. Se eligió un TTL más largo que el de inventario precisamente porque la inestabilidad de este dato es menor: el costo de una lectura desactualizada es bajo (nadie toma una decisión operativa crítica en el margen de 2 minutos) frente al ahorro de no filtrar el JSON completo en cada petición.

## 4. Qué no se cacheó y por qué

Ver la lista completa en la sección "Explícitamente no cacheado" del punto 2. En resumen, se excluyó cualquier endpoint donde: (a) la respuesta depende del usuario o la sesión autenticada (riesgo de fuga de datos con una clave compartida), (b) el endpoint escribe datos, (c) el resultado no es repetible entre peticiones (análisis de un CSV recién subido), o (d) cachear reforzaría un problema de autorización ya identificado y pendiente de otra auditoría (`GET /users`). También se dejó fuera `get_current_user` por tocar el hot path de autenticación, priorizando dos decisiones bien acotadas y seguras sobre una cuarta de mayor riesgo.
