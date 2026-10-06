"use client";

import { useAuth } from "./auth-provider";
import { useWorkspaceData } from "./workspace-data";

/** Compatibility facade for page components. Data is loaded once by the authenticated shell. */
export function useDashboardData() {
  const { isLoading: authLoading } = useAuth();
  const workspace = useWorkspaceData();
  return {
    ...workspace,
    isLoading: authLoading || workspace.isLoading,
  };
}
