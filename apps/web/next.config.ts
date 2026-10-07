import type { NextConfig } from "next";

// `standalone` output is required by the production container image (Dockerfile.prod).
// On Windows developer machines the standalone trace step needs symlink privileges that
// are off by default, which aborts an otherwise successful build. Set
// NEXT_DISABLE_STANDALONE=1 for local builds; the container build never sets it.
const disableStandalone = process.env.NEXT_DISABLE_STANDALONE === "1";

const nextConfig: NextConfig = {
  ...(disableStandalone ? {} : { output: "standalone" }),
};

export default nextConfig;
