"use client";

import { useEffect, useRef } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuth } from "./auth-provider";

interface AuthGuardProps {
  children: React.ReactNode;
  fallback?: React.ReactNode;
}

export function AuthGuard({ children, fallback = null }: AuthGuardProps) {
  const { token, organization, isLoading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  
  // Use ref to track if we've already attempted redirect to prevent duplicate redirects
  const hasAttemptedRedirectRef = useRef(false);

  // Separate effect for initial auth check - only run once on mount or when auth state changes
  useEffect(() => {
    // Only redirect if actually unauthenticated (not just loading)
    if (!isLoading && (!token || !organization)) {
      // Prevent multiple redirects
      if (!hasAttemptedRedirectRef.current) {
        hasAttemptedRedirectRef.current = true;
        const loginUrl = `/login?redirect=${encodeURIComponent(pathname)}`;
        router.replace(loginUrl);
      }
    } else if (token && organization) {
      // Reset redirect flag when authenticated
      hasAttemptedRedirectRef.current = false;
    }
  }, [isLoading, token, organization, pathname, router]);

  if (isLoading) {
    return fallback ?? <div className="console-loading">Loading...</div>;
  }

  if (!token || !organization) {
    return fallback ?? <div className="console-loading">Redirecting to login...</div>;
  }

  return <>{children}</>;
}

export function OptionalAuth({ children }: { children: React.ReactNode }) {
  const { isLoading } = useAuth();

  if (isLoading) {
    return <div className="console-loading">Loading...</div>;
  }

  return <>{children}</>;
}
