# REPORT.md — Auditoría de Rendimiento Frontend (Nexova Solutions)

Este reporte documenta las correcciones aplicadas a partir de [`AUDIT.md`](./AUDIT.md), su impacto medido, y — lo más importante — **qué causó realmente la mejora observada**.

> **Resumen en una línea:** la mejora medida (86→100 en el website, 67→100 en el backoffice) es atribuible a **medir contra un build de producción en lugar del servidor de desarrollo**, no a los cambios de código. Lo demostramos con un experimento de aislamiento (sección 4).

## 1. Correcciones aplicadas

### 1.1 `RequireAuth` — desacoplar el render de la llamada a `/api/auth/me`

**Archivo:** [`uis/backoffice/components/RequireAuth.tsx`](./uis/backoffice/components/RequireAuth.tsx)

**Antes:** el gate de autenticación esperaba a que `AuthContext` resolviera una llamada de red a `/api/auth/me` antes de pintar cualquier contenido, incluido el elemento LCP.

**Después:** decide de forma síncrona leyendo el token de `localStorage`. La validación contra el backend sigue ocurriendo en paralelo (para poblar `user`), y el manejo de 401 ya existente en `lib/api.ts` sigue cubriendo el caso de token inválido — sin cambios en el comportamiento de seguridad (verificado: una visita sin sesión a `/inventory/products` sigue redirigiendo a `/login`).

**Clasificación honesta: mejora de arquitectura, NO de rendimiento.** Se midió su impacto de forma aislada y es indistinguible del ruido (sección 4). Se mantiene porque es código más simple y elimina una dependencia de red del camino de render — algo que sí importaría con una API remota de alta latencia — pero **no se le puede atribuir ninguna de las mejoras de puntuación observadas.**

### 1.2 Extracción de `AppShell` compartido

**Archivo nuevo:** [`uis/backoffice/components/AppShell.tsx`](./uis/backoffice/components/AppShell.tsx)

`(inventory)/layout.tsx` e `(incidents)/layout.tsx` duplicaban casi línea por línea el mismo header, fondo y wrapper de `RequireAuth`. Se extrajo un componente parametrizado (`eyebrow`, `subtitle`, `tabs?`) reutilizado por ambos. Mismo HTML y clases resultantes, sin cambio visual ni funcional.

Es el entregable de refactorización pedido por el enunciado. No tiene ni pretende tener impacto en Lighthouse: reduce deuda técnica, no tiempo de carga.

### 1.3 `prefetch={false}` en la navegación interna

**Archivos:** [`(dashboard)/layout.tsx`](<./uis/backoffice/app/(dashboard)/layout.tsx>), `AppShell.tsx`

Los enlaces del sidebar precargaban el JS de otras route-groups aunque el usuario nunca navegara ahí. Se desactivó ese prefetch. **Se midió y el impacto fue nulo** (mismos LCP/TBT antes y después en dev). Se mantiene como buena práctica para un panel interno de bajo tráfico, no como optimización.

## 2. Metodología

Las capturas "before" (`audit/before/`) se tomaron contra el servidor de **desarrollo** (`next dev`), que sirve JS sin minificar, sin code-splitting optimizado y compila cada ruta bajo demanda. Eso no representa el rendimiento real de la aplicación.

Las capturas "after" (`audit/after/`) se tomaron contra un **build de producción** (`next build && next start`), usando el mismo navegador, las mismas URLs de Codespaces y el mismo método (panel Lighthouse de DevTools) que las "before", y con **sesión autenticada real** en las páginas del backoffice.

Ambos conjuntos comparten el mismo entorno y método salvo por la variable que nos interesa (dev vs producción), lo que hace la comparación directa.

## 3. Comparativa de puntuaciones (before dev → after producción)

| Página | Modo | Performance | LCP | TBT |
|---|---|---|---|---|
| Website — Home | Desktop | 86 → **100** | 0.5s → 0.5s | 340ms → **0ms** |
| Website — Home | Móvil | 72 → **99** | 2.2s → 2.2s | 1680ms → **10ms** |
| Website — /application | Desktop | 83 → **100** | 0.5s → 0.3s | 400ms → **0ms** |
| Website — /application | Móvil | — | — | — |
| Backoffice — Dashboard | Desktop | 67 → **100** | 3.0s → n/d | 360ms → n/d |
| Backoffice — Dashboard | Móvil | 45 → **99** | 16.5s → n/d | 1550ms → n/d |
| Backoffice — Inventario | Desktop | 67 → **100** | 3.0s → 0.5s | 370ms → **0ms** |
| Backoffice — Inventario | Móvil | — | — | — |

Notas de honestidad sobre esta tabla:
- Las filas de `/application` móvil e Inventario móvil solo tienen captura "after" (99 en ambas); no hay "before" equivalente, así que no son comparables.
- "n/d" indica que la captura "after" correspondiente muestra el círculo de puntuación pero no el panel de métricas, así que no se transcriben valores que no aparecen en la evidencia.
- El SEO permanece en 60 en todas las capturas, antes y después: es un artefacto del proxy de preview de Codespaces (`noindex` en las URLs de túnel), verificado en 100/100 al auditar contra `localhost`. Ver [`AUDIT.md`](./AUDIT.md) §2 y §4.2. Al estar presente por igual en ambos conjuntos, no distorsiona la comparación.

## 4. Experimento de aislamiento: ¿qué causó realmente la mejora?

La tabla anterior mezcla dos variables: se cambió el código **y** se cambió el modo de build. Para separarlas, se midió el dashboard autenticado con un build de producción **con y sin** el fix de `RequireAuth` (3 corridas de cada configuración, Lighthouse programático):

**Desktop**

| Corrida | Sin el fix | Con el fix |
|---|---|---|
| Performance | 100 / 100 / 100 | 100 / 100 |
| LCP | 0.7s / 0.6s / 0.6s | 0.7s / 0.7s |
| TBT | 20ms / 0ms / 10ms | 0ms / 0ms |

**Móvil** (throttling por defecto de Lighthouse: 4x CPU + red simulada)

| Corrida | Sin el fix | Con el fix |
|---|---|---|
| Performance | 90 / 93 / 90 | 83 / 93 / 94 |
| LCP | 3.1s / 2.9s / 3.0s | 3.0s / 2.8s / 2.9s |
| TBT | 220ms / 150ms / 230ms | 440ms / 160ms / 140ms |

**Conclusión:** la variación entre corridas de la *misma* configuración (de 83 a 94 con el fix) es mayor que la diferencia entre configuraciones. Es ruido, no señal. **El fix de `RequireAuth` no produce una mejora medible.**

**Por qué:** la API corre en la misma red Docker (`http://api:8000`), así que el round-trip a `/api/auth/me` tarda ~10-20ms. El razonamiento mecánico del hallazgo era correcto (el gate efectivamente bloqueaba el render), pero la magnitud era irrelevante frente a los ~3s de overhead del dev-server. Con una API remota de alta latencia el fix sí importaría, pero eso no se puede demostrar en este entorno y por tanto no se afirma.

**Evidencia adicional en la misma dirección:** el website pasó de 86 a 100 **sin que se modificara una sola línea de su código**. Es la demostración más limpia de que la mejora proviene del build de producción.

## 5. Valoración de impacto

Ordenado por valor real aportado, no por vistosidad:

1. **Corrección metodológica (el hallazgo principal).** La auditoría inicial medía el servidor de desarrollo, lo que producía un diagnóstico falso: parecía que el backoffice tenía un problema grave de rendimiento (45 en móvil, LCP de 16.5s) cuando en producción rinde 99-100. Detectar esto evitó "optimizar" un problema que no existía en producción.
2. **Diagnóstico del artefacto de SEO.** El 60 constante en las 6 páginas no era un defecto de código sino del proxy de Codespaces (verificado en 100/100 contra `localhost`). Evita perseguir un bug inexistente.
3. **Refactorización de `AppShell`.** Elimina duplicación real entre dos layouts; su valor es de mantenibilidad.
4. **Mejora de arquitectura en `RequireAuth`.** Código más simple y sin dependencia de red en el render, aunque sin impacto medible en este entorno.
5. **`prefetch={false}`.** Medido, sin impacto. Se conserva por criterio, no por datos.

**Lo que esta auditoría NO logró:** no se encontró ni corrigió ningún cuello de botella de rendimiento real en producción, porque no lo había — ambos frontends ya rendían bien al construirse correctamente. El resultado honesto de una auditoría puede ser "el sistema está sano y la medición estaba mal hecha".

## 6. Trabajo pendiente identificado

- **Dashboard en móvil, producción: LCP de ~2.9s**, por encima del umbral recomendado de 2.5s, con TBT de ~150-230ms. Es el único margen de mejora real detectado y no se abordó en esta iteración. Sería el punto de partida natural para una segunda vuelta.
- **`next dev` requiere `--webpack`** en este proyecto porque Turbopack entra en panic recurrente en este entorno Docker/Codespaces (`Next.js package not found` en el ciclo de HMR). Resolverlo aceleraría el desarrollo, aunque no afecta a producción.
- **El backoffice no declara `noindex`.** No afecta a Lighthouse, pero al ser un panel interno convendría añadir `robots: { index: false }` en su `metadata` antes de un despliegue en dominio público.
