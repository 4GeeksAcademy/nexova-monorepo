import path from "node:path";

const isDev = process.env.NODE_ENV !== "production";

/** Origen (`scheme://host:port`) de una URL configurada, o null si no es válida. */
function originOf(url) {
  if (!url) return null;
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

/**
 * Orígenes a los que el navegador habla directamente (además de `'self'`, que
 * cubre los route handlers proxy `/api/*`). Igual que las `NEXT_PUBLIC_*`, se
 * resuelven en `next build`: si cambian, hay que reconstruir.
 * - Chat por WebSocket: `NEXT_PUBLIC_CHAT_WS_URL`, o el fallback de
 *   `lib/chat-socket.ts` (`ws(s)://<host>:8000`) en local.
 * - Telemetría: `NEXT_PUBLIC_TELEMETRY_ENDPOINT`.
 */
function connectSources() {
  const sources = new Set(["'self'"]);
  const chat = originOf(process.env.NEXT_PUBLIC_CHAT_WS_URL);
  if (chat) {
    sources.add(chat);
  } else {
    for (const host of ["localhost:8000", "127.0.0.1:8000"]) {
      sources.add(`ws://${host}`);
      sources.add(`wss://${host}`);
    }
  }
  const telemetry = originOf(process.env.NEXT_PUBLIC_TELEMETRY_ENDPOINT);
  if (telemetry) sources.add(telemetry);
  // HMR de `next dev` (WebSocket al mismo host; algunos navegadores no lo cubren con 'self').
  if (isDev) sources.add("ws://localhost:*").add("ws://127.0.0.1:*");
  return [...sources].join(" ");
}

const contentSecurityPolicy = [
  "default-src 'self'",
  // Next inyecta scripts inline de hidratación sin nonce; React dev necesita eval.
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src ${connectSources()}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()",
  },
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  poweredByHeader: false,
  transpilePackages: ["@hito2-logic"],
  experimental: {
    externalDir: true,
  },
  turbopack: {
    root: path.resolve(process.cwd(), "..", ".."),
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
