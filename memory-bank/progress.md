## Estado actual del desarrollo
- El alcance del proyecto ya está definido: automatizar el pre-cribado de candidatos con IA, scoring/ranking de CVs y apoyo con búsqueda semántica.
- Ya existe una base funcional de interfaz para seguimiento de candidaturas (Talent Pipeline Tracker) y una capa de integración API en frontend.
- El contexto de negocio y técnico está documentado en project brief y tech context.
- El proyecto se encuentra en fase de transición entre definición y ejecución del MVP técnico.
- Aún falta consolidar trazabilidad de avances, hitos y bloqueos dentro del archivo de progreso.
- Se completó un refactor visual del dashboard financiero en `uis/backoffice` con una estética dark premium (paleta cohesiva, jerarquía tipográfica refinada, KPIs con iconografía y bloque JSON estilo terminal).

## Pasos previstos
- Establecer baseline del sprint actual (fecha de inicio, objetivo, fecha de corte y criterio de éxito).
- Definir backlog inmediato del MVP con prioridades claras.
- Implementar el flujo mínimo de pre-cribado de extremo a extremo (ingesta, análisis, scoring y salida).
- Integrar resultado del scoring con la vista de candidaturas para validación operativa.
- Definir métricas de validación inicial (tiempo de cribado, consistencia del ranking, reducción de carga manual).
- Registrar riesgos y dependencias técnicas activas (calidad de datos, disponibilidad de API, reglas de evaluación).
- Planificar siguiente iteración con mejoras sobre precisión, explicabilidad y automatización de comunicaciones.
- Validar con stakeholders de negocio la nueva línea visual premium del backoffice antes de extenderla a otros módulos.

## Historial de Avances
- **[2026-09-06] Dockerización del monorepo para desarrollo (ticket #infra-40):** Se añadieron `Dockerfile`/`.dockerignore` en `/uis` (website + backoffice en un único contenedor Node, arrancados vía `start.sh` en puertos 3000/3001 con hot reload) y en `/services` (FastAPI + `uv` + uvicorn `--reload`), orquestados con `docker-compose.yml` en la raíz sobre una red Docker explícita (`nexova-net`). Variables centralizadas en `.env` raíz (plantilla en `.env.example`), comunicación entre contenedores por nombre de servicio (`BACKEND_API_URL=http://api:8000`, nunca `localhost`). Se resolvieron dos acoplamientos cruzados fuera del contexto de build de cada Dockerfile vía bind mounts en runtime: `packages/incidents-analyzer` (usado por el fallback de `sys.path` en `app/main.py`) y la carpeta raíz `src/` (usada por el alias `@hito2-logic` del backoffice). Se detectó y corrigió además un bug de Turbopack en `uis/backoffice/next.config.mjs` (`turbopack.root` no fijado): la resolución de `@hito2-logic/*` funcionaba en local por casualidad (Turbopack encontraba un lockfile ancestro en la raíz real del repo) pero fallaba dentro del contenedor al no existir un lockfile por encima de `/app/backoffice`; se fijó `turbopack.root` de forma dinámica vía `process.cwd()` para que funcione en ambos entornos. Probado end-to-end en un Codespace de GitHub: `docker compose up --build` levanta los 3 servicios (website/backoffice/api) sin pasos manuales adicionales salvo rellenar `.env`; login/registro y módulos de inventario e incidencias confirmados contra el backend vía nombre de servicio; hot reload verificado en website, backoffice, `src/` compartido y backend. PR abierto: `feature/dockerizacion` → `main` en `4GeeksAcademy/nexova-monorepo`.
- **[2026-09-04] Validación estricta de emails en API:** Se sustituyeron campos Pydantic de email basados en `str` por `EmailStr` en modelos de usuarios/autenticación y en el contacto de proveedores. Se declaró `email-validator` como dependencia del servicio API y se validó que los modelos rechazan emails inválidos.
- **[2026-07-25] Refactor visual premium del backoffice:** Rediseño de `app/layout.tsx` y `app/page.tsx` con dark mode elegante, tarjetas KPI modernizadas, header sin banner agresivo y panel JSON con look de terminal.