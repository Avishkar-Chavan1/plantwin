import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "../components/auth-provider";

export const metadata: Metadata = {
  title: "ProcessTwin | Industrial intelligence",
  description: "Physics-informed industrial process optimization"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}