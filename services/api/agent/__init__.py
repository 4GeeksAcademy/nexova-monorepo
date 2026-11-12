"""Agente de soporte de Nexova: el RAG de `data/pipelines/rag.py` como grafo de LangGraph.

- `state.py`: estado mínimo que viaja entre nodos.
- `nodes.py`: un nodo por responsabilidad (recepción, recuperación, generación…).
- `graph.py`: aristas condicionales, compilación explícita y checkpointer SQLite.
- `tracing.py`: trace JSON consultable de cada corrida.
- `runner.py`: punto de entrada único (`run_agent`) para la API y los scripts.
- `router.py`: `POST /agent/query` (solo HTTP).
"""
