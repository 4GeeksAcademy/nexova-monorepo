"""
Memoria persistente del agente (Hito 8 · Parte 1). Ver docs/agent/memory-design.md.

- `schemas`: contratos (propuesta, decisión, hecho, auditoría).
- `policy`: qué nunca entra en memoria (BRIEFING de Nexova).
- `store`: interfaz de lectura/escritura (`MemoryStore`) y backends Postgres / en memoria.
- `generation`: respuesta + `propuesta_memoria` en una sola llamada.
- `classifier`: decisión del usuario frente a la propuesta pendiente.
- `consolidation`: deduplicación, TTL, topes y relevancia.
- `service`: `AgentMemory`, el ciclo completo.
- `nodes`: nodos del grafo.
"""
