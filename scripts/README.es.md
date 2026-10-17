# Carpeta `scripts`

Esta carpeta contiene **scripts auxiliares** del monorepo: automatizaciones de desarrollo, utilidades de mantenimiento, tareas repetitivas (setup, lint, migraciones, generación de datos, etc.) y tooling interno.

- **Propósito principal**: agrupar herramientas de soporte que no pertenecen a una app/agente/pipeline específico, pero facilitan el trabajo del equipo.
- **Recomendación**: documenta cada script (qué hace, parámetros, requisitos, ejemplos de uso) y procura que sean reproducibles (y seguros) en distintos entornos.

## Arranque de Docker en Codespaces

Desde la raíz del monorepo:

```bash
bash scripts/docker-up.sh
# Para reconstruir las imágenes:
bash scripts/docker-up.sh --build
```

El script arranca Compose y corrige el conflicto observado en Codespaces entre
las reglas modernas de Docker y un firewall `iptables-legacy` con política
`FORWARD DROP`. Este conflicto impide la salida a Internet de `nexova-net`,
incluida la resolución DNS de Supabase, aunque el bridge predeterminado funcione.

La reparación requiere `sudo` sin contraseña y solo permite salida del bridge
actual de `nexova-net` y tráfico de retorno `RELATED,ESTABLISHED` en la cadena
legacy `DOCKER-USER`. No cambia políticas globales, no borra reglas existentes y
no duplica permisos en ejecuciones posteriores. Reinicia la API si añade reglas,
para reintentar el arranque fallido. En entornos sin el conflicto detectable,
solo ejecuta Compose; no solicita contraseñas ni modifica el firewall.

Los permisos pertenecen al entorno, no a la imagen. Vuelve a ejecutar este
comando tras recrear la red o reiniciar el Codespace. Conserva el DNS de Docker;
no es necesario configurar DNS públicos ni fijar direcciones IP de Supabase.

```bash
docker compose logs --tail=80 api
curl --fail http://localhost:8000/api/health
```

## Script nocturno de telemetría (`nightly_export.py`)

Ticket #DEV-53. Para el día objetivo (por defecto, **ayer en UTC**):

1. Exporta las filas de `telemetry_events` de ese día a
   `data/raw/telemetry_YYYY-MM-DD.csv`, solo si el archivo no existe. Es un
   backup de auditoría: el pipeline sigue leyendo de la base de datos.
2. Lanza `data/pipelines/pipeline.py --trigger schedule --week-start <lunes>`
   como subproceso (recalcula la semana del día objetivo y la anterior).
3. Registra la ejecución en `public.job_runs`
   (`pending → processing → completed | failed`) vía `services/job_runner/`.

Requisitos: `DATABASE_URL` y la migración `services/api/migrations/004_job_runs.sql`
aplicada (a mano, igual que la 002 y la 003).

```bash
python scripts/nightly_export.py
TARGET_DATE=2026-10-14 python scripts/nightly_export.py   # fecha arbitraria
```

Comportamiento:

- **Lock**: si ya hay una fila `processing` de `nightly_export`, la instancia
  nueva registra `status=aborted` en el log y sale con código 0. Un índice
  único parcial sobre `status = 'processing'` impide que dos instancias lleguen
  a la vez. Una fila `processing` de más de 2 h (proceso muerto con SIGKILL)
  se cierra como `failed` en la siguiente ejecución.
- **Idempotencia**: si el día ya tiene una fila `completed`, no se exporta ni se
  lanza el pipeline (`status=skipped`). Un día `failed` sí se reintenta.
- **Fallos**: cualquier excepción (o SIGTERM) deja la fila en `failed` con el
  mensaje de error y el script sale con código 1.

En Docker lo dispara el servicio `scheduler` (supercronic) con el cron de
`services/scheduler/crontab`: `30 2 * * *` (02:30 UTC). Para ejecutarlo a mano
dentro del contenedor:

```bash
docker compose exec scheduler python /repo/scripts/nightly_export.py
docker compose logs --tail=50 scheduler
```
