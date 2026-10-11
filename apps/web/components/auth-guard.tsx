"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "./auth-provider";

/**
 * Renders children only for a restored, valid session. Unauthenticated visitors are replaced
 * (not pushed) onto /login with the original path preserved, so Back never loops.
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { token, organization, isLoading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const authenticated = Boolean(token && organization);

  useEffect(() => {
    if (!isLoading && !authenticated) {
      router.replace(`/login?redirect=${encodeURIComponent(pathname)}`);
    }
  }, [authenticated, isLoading, pathname, router]);

  if (isLoading) return <div className="loading-shell" role="status">Restoring secure session…</div>;
  if (!authenticated) return <div className="loading-shell" role="status">Redirecting to sign in…</div>;
  return <>{children}</>;
}
