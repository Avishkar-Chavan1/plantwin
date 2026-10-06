import { AuthGuard } from "@/components/auth-guard";
import { AuthenticatedShell } from "@/components/authenticated-shell";
import { ReactNode } from "react";

export default function AuthenticatedLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <AuthGuard>
      <AuthenticatedShell>
        {children}
      </AuthenticatedShell>
    </AuthGuard>
  );
}
