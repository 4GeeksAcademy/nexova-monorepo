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
