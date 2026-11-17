import ChatPanel from "@/components/chat/ChatPanel";

/**
 * Chat con el agente de soporte de primera línea, por WebSocket: la respuesta
 * llega token a token y se puede interrumpir a mitad para redirigirla.
 * (Antes: una pregunta y la respuesta completa por `POST /knowledge/query`.)
 */
export default function KnowledgePage() {
  return <ChatPanel />;
}
