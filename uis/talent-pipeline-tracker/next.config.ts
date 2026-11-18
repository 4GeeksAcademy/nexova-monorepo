import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV !== "production";

/** Origen (`scheme://host:port`) de una URL configurada, o null si no es válida. */
function originOf(url: string | undefined): string | null {
  if (!url) return null;
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

/**
 * Orígenes a los que el navegador habla directamente, además de `'self'`.
 * - API de candidatos: `NEXT_PUBLIC_API_URL`.
 * - API de autenticación: `NEXT_PUBLIC_AUTH_API_URL`.
 * Igual que las `NEXT_PUBLIC_*`, se resuelven en `next build`: si cambian, hay que reconstruir.
 */
function connectSources(): string {
  const sources = new Set<string>(["'self'"]);
  for (const url of [process.env.NEXT_PUBLIC_API_URL, process.env.NEXT_PUBLIC_AUTH_API_URL]) {
    const origin = originOf(url);
    if (origin) sources.add(origin);
  }
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

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
