# AUDIT.md — Auditoría de Rendimiento Frontend (Nexova Solutions)

## 1. Alcance

Se auditaron los dos frontends del monorepo:

- **Sitio corporativo** (`uis/website`) — páginas: `/` (home) y `/application` (formulario de solicitud de servicios).
- **Backoffice** (`uis/backoffice`) — páginas: `/` (Dashboard Financiero) e `/inventory/products` (vista con tabla de datos, la más compleja del panel).

Herramienta: Lighthouse (panel DevTools de Chrome), en modo Desktop y Móvil donde aplica.

## 2. Metodología y advertencia importante

Las capturas "before" (`audit/before/`) se tomaron contra las URLs de preview de GitHub Codespaces (`*.app.github.dev`), apuntando al servidor de **desarrollo** (`next dev`), no a un build de producción. Esto es relevante para interpretar los números:

- **El SEO=60 constante en las 6 páginas es un artefacto del entorno, no un defecto de código.** Se verificó ejecutando Lighthouse (solo categoría SEO) contra `http://localhost:3000` directamente: el resultado fue **100/100**. El título y `meta description` ya están correctamente definidos en ambos `layout.tsx` (`uis/website/my-app/app/layout.tsx` y `uis/backoffice/app/layout.tsx`). La penalización viene del proxy de preview de Codespaces (probablemente cabeceras `noindex` en las URLs de túnel), no del código de la aplicación.
- **Los tiempos de Performance están inflados por medir en modo dev**, que sirve JS sin minificar y con el runtime de Fast Refresh. Se confirmó repitiendo la medición en local contra `next dev`: los números se mantienen en el mismo rango deficiente. Para la medición "after" se recomienda repetir contra `next build && next start`, que dará una comparación más representativa de producción.

Estas dos advertencias no invalidan la auditoría — de hecho, aislaron cuáles de los problemas observados son reales (persisten en cualquier entorno) y cuáles son ruido de la herramienta/entorno.

## 3. Puntuaciones iniciales

| Página | Modo | Performance | Accessibility | Best Practices | SEO | LCP | TBT | CLS |
|---|---|---|---|---|---|---|---|---|
| Website — Home | Desktop | 86 | 100 | 100 | 60 | 0.5s | 340ms | 0 |
| Website — Home | Móvil | 72 | 100 | 100 | 60 | 2.2s | 1680ms | 0.039 |
| Website — /application | Desktop | 83 | 100 | 100 | 60 | 0.5s | 400ms | 0 |
| Backoffice — Dashboard | Desktop | 67 | 96 | 100 | 60 | 3.0s | 360ms | 0 |
| Backoffice — Dashboard | Móvil | **45** | 95 | 100 | 60 | **16.5s** | 1550ms | 0 |
| Backoffice — Inventario | Desktop | 67 | 98 | 100 | 60 | 3.0s | 370ms | 0 |

Capturas completas en [`audit/before/`](./audit/before/).

## 4. Problemas identificados y causa raíz

### 4.1 [Hipótesis posteriormente REFUTADA] LCP alto en el backoffice — gate de autenticación client-side bloquea el render

> ⚠️ **Nota posterior:** esta hipótesis se formuló durante la auditoría y resultó **incorrecta en cuanto a su magnitud**. Se midió de forma aislada (build de producción, con y sin la corrección) y la diferencia es indistinguible del ruido: el round-trip a `/api/auth/me` tarda ~10-20ms contra una API en la misma red Docker, irrelevante frente a los ~3s de overhead del dev-server que causaban realmente el LCP observado. Ver [`REPORT.md`](./REPORT.md) §4. Se conserva el análisis original por transparencia del proceso.

**Síntoma:** el LCP del backoffice es 6x peor que el del website (3.0s vs 0.5s en desktop; 16.5s vs 2.2s en móvil), a pesar de que ambas apps comparten stack (Next 16, sin librerías pesadas, sin imágenes).

**Causa raíz:** todas las rutas protegidas (`(dashboard)`, `(inventory)`, `(incidents)`) están envueltas en `<RequireAuth>` ([`components/RequireAuth.tsx`](./uis/backoffice/components/RequireAuth.tsx)), que depende de `AuthContext` ([`lib/AuthContext.tsx`](./uis/backoffice/lib/AuthContext.tsx)):

```
AuthProvider monta con loading=true
  → useEffect dispara fetchMe() (llamada de red a /api/auth/me)
  → hasta que esa promesa resuelve, loading sigue en true
RequireAuth no pinta children mientras loading=true
  → el <h2>Dashboard Financiero</h2> (elemento LCP) no existe en el DOM hasta entonces
```

El contenido real de la página —incluido el elemento que Lighthouse mide como LCP— queda detrás de un waterfall 100% en cliente: descarga de JS → hidratación → round-trip de red a `/api/auth/me` → recién ahí render. En móvil, con CPU y red throttled, ese waterfall se dispara a 16.5s.

**Por qué es real (no artefacto):** este patrón es independiente de dev/prod y del entorno de Codespaces — es arquitectura de la app. Se repite igual en `(dashboard)/layout.tsx`, `(inventory)/layout.tsx` e `(incidents)/layout.tsx`.

### 4.2 [Entorno, documentado] SEO=60 uniforme

Ver sección 2. Confirmado como artefacto del proxy de preview de Codespaces; localmente da 100/100. No requiere corrección de código — se documenta para no perseguir un problema inexistente.

### 4.3 [Entorno, documentado] TBT elevado (340ms–1680ms) y Performance deprimido en general

Ver sección 2. Medido contra `next dev`. Se recomienda re-medir contra build de producción antes de decidir si queda trabajo adicional de optimización de JS más allá del punto 4.1.

### 4.4 CLS y Accessibility — sin hallazgos relevantes

CLS es 0 (o 0.039, despreciable) en las 6 páginas — no hay layout shift que corregir. Accessibility ya está en 95-100 en todas — sin acción prioritaria.

## 5. Análisis de refactorización (código duplicado)

### 5.1 Layout de shell duplicado entre `(inventory)` y `(incidents)`

[`app/(inventory)/layout.tsx`](<./uis/backoffice/app/(inventory)/layout.tsx>) y [`app/(incidents)/layout.tsx`](<./uis/backoffice/app/(incidents)/layout.tsx>) repiten casi línea por línea:

- El mismo fondo: `bg-[linear-gradient(180deg,#f7f5ef_0%,#efe8dc_100%)] text-stone-900`.
- La misma estructura de `<header>`: `border-b border-stone-300/80 bg-stone-50/75 backdrop-blur-xl` con un contenedor `mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-4`.
- El mismo `<AccountMenu variant="light" />` y el mismo `<RequireAuth>`.

Solo cambia el contenido de navegación interno. **Propuesta:** extraer un componente compartido `AppShell` (o `SectionHeader` + wrapper), parametrizado por `title`, `subtitle` y `nav`/`actions`, reutilizado por ambos layouts (y candidato natural para `(dashboard)/layout.tsx` también, aunque su header visualmente difiere más).

### 5.2 Patrón de auth-gate repetido 3 veces

`<RequireAuth>{...}</RequireAuth>` envuelve el `children` completo en los tres layouts protegidos, con la misma lógica de "esperar sesión antes de pintar nada". Aunque `RequireAuth` en sí ya es un componente compartido (buena práctica existente), el *patrón de uso* — envolver todo el árbol, incluido el shell estático que no depende de la sesión — está repetido en los tres.

> ⚠️ **Nota posterior:** la versión original de esta sección afirmaba que este patrón era "la causa directa del problema 4.1" y que corregirlo resolvería el rendimiento. Eso resultó **falso**: la medición aislada no muestra mejora atribuible (ver [`REPORT.md`](./REPORT.md) §4). El argumento de deduplicación sigue siendo válido; el de rendimiento no.

## 6. Plan de corrección (resultados en [`REPORT.md`](./REPORT.md))

Prioridad sugerida en el momento de la auditoría, con el resultado real de cada punto anotado después:

1. Desacoplar el render del gate de `RequireAuth` para que el LCP no dependa de la llamada a `/api/auth/me`. → **Aplicado, pero sin impacto medible** (REPORT.md §4). Se conserva como mejora de arquitectura.
2. Extraer `AppShell` compartido entre `(inventory)` e `(incidents)` (resuelve 5.1). → **Aplicado.**
3. Re-medir Performance contra build de producción (`next build && next start`) para una comparación before/after representativa. → **Aplicado, y resultó ser el hallazgo principal de toda la auditoría**: la mejora observada proviene enteramente de aquí (REPORT.md §5).
