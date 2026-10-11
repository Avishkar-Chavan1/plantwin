import type { NextConfig } from "next";

// The browser talks directly to the separately deployed API. NEXT_PUBLIC_* values are compiled
// into client JavaScript, so this must be a public origin and must never contain a secret.
const apiUrl = (process.env.NEXT_PUBLIC_API_URL ?? "").trim().replace(/\/+$/, "");
const isHostedBuild = Boolean(process.env.VERCEL);

if (isHostedBuild && !apiUrl) {
  throw new Error(
    "NEXT_PUBLIC_API_URL is not set. Configure it to the public HTTPS origin of the ProcessTwin API " +
      "(for example https://api.example.com) in the hosting provider's environment variables; " +
      "without it the frontend would call http://localhost:8000.",
  );
}
if (apiUrl) {
  let parsed: URL;
  try {
    parsed = new URL(apiUrl);
  } catch {
    throw new Error(`NEXT_PUBLIC_API_URL is not a valid absolute URL: ${apiUrl}`);
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("NEXT_PUBLIC_API_URL must be a bare origin: no credentials, query string or fragment.");
  }
  const isProductionDeploy = process.env.VERCEL_ENV === "production";
  if (isProductionDeploy && parsed.protocol !== "https:") {
    throw new Error("NEXT_PUBLIC_API_URL must use https:// for production deployments.");
  }
}

const connectSources = ["'self'", apiUrl || "http://localhost:8000"].join(" ");
const isDev = process.env.NODE_ENV !== "production";

// Next.js hydrates through inline bootstrap scripts, so 'unsafe-inline' is required for scripts
// without a per-request nonce pipeline. Everything else is locked down: no framing, no plugins,
// connections only to this origin and the API origin.
const contentSecurityPolicy = [
  "default-src 'self'",
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

// `standalone` output is required by the production container image (Dockerfile.prod). On Windows
// developer machines the standalone trace step needs symlink privileges that are off by default;
// set NEXT_DISABLE_STANDALONE=1 for local builds. Vercel ignores `standalone` and builds its own
// output, so it is disabled there as well.
const disableStandalone = process.env.NEXT_DISABLE_STANDALONE === "1" || Boolean(process.env.VERCEL);

const nextConfig: NextConfig = {
  ...(disableStandalone ? {} : { output: "standalone" }),
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: contentSecurityPolicy },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;