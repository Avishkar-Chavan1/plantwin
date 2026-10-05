"use client";

import { useEffect, useState } from "react";
import { useAuth } from "./auth-provider";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface DashboardData {
  summary: any;
  plants: any[];
  isLoading: boolean;
  message: string;
  setMessage: (msg: string) => void;
  token: string | null;
  organization: string | null;
}

export function useDashboardData(): DashboardData {
  const { token, organization, isLoading } = useAuth();
  const [summary, setSummary] = useState<any>(null);
  const [plants, setPlants] = useState<any[]>([]);
  const [message, setMessage] = useState("");
  const [isLoadingData, setIsLoadingData] = useState(true);

  const headers = token && organization
    ? { Authorization: `Bearer ${token}`, "X-Organization-ID": organization, "Content-Type": "application/json" }
    : undefined;

  useEffect(() => {
    if (!headers) return;
    const loadData = async () => {
      setIsLoadingData(true);
      try {
        const [summaryRes, plantsRes] = await Promise.all([
          fetch(`${apiUrl}/api/v1/dashboard/summary`, { headers }),
          fetch(`${apiUrl}/api/v1/plants`, { headers }),
        ]);
        if (summaryRes.ok) setSummary(await summaryRes.json());
        if (plantsRes.ok) {
          const data = await plantsRes.json();
          setPlants(data.items ?? []);
        }
      } catch {
        // ignore
      } finally {
        setIsLoadingData(false);
      }
    };
    loadData();
  }, [headers]);

  return {
    summary,
    plants,
    isLoading: isLoading || isLoadingData,
    message,
    setMessage,
    token,
    organization,
  };
}