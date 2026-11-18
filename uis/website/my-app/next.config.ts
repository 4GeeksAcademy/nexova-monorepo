import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV !== "production";

/**
 * La web pública no llama a APIs externas desde el navegador: solo `'self'`
 * (más el WebSocket de HMR en `next dev`).
 */
const connectSources = isDev ? "'self' ws://localhost:* ws://127.0.0.1:*" : "'self'";

const contentSecurityPolicy = [
  "default-src 'self'",
  // Next inyecta scripts inline de hidratación sin nonce; React dev necesita eval.
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src ${connectSources}`,
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
