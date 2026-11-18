"""Resume `docker compose config --format json` (stdin): puertos en el host, usuario y perfil por servicio."""

import json
import sys

cfg = json.load(sys.stdin)
for name, svc in sorted(cfg.get("services", {}).items()):
    ports = [f"{p.get('host_ip') or '0.0.0.0'}:{p.get('published')}->{p.get('target')}" for p in svc.get("ports", [])]
    profiles = ",".join(svc.get("profiles", [])) or "-"
    print(f"{name:10s} perfil={profiles:6s} user={svc.get('user', '(imagen)'):12s} {' '.join(ports) or '(sin puertos en el host)'}")
