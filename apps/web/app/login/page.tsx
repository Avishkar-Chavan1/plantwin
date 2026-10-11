"use client";

import { FormEvent, Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/components/auth-provider";
import { safeRedirectTarget } from "@/components/api-client";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { login, token, organization, isLoading } = useAuth();
  const [message, setMessage] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const destination = safeRedirectTarget(searchParams?.get("redirect"));

  // Visiting /login while already signed in just continues into the application.
  useEffect(() => {
    if (!isLoading && token && organization) router.replace(destination);
  }, [destination, isLoading, organization, router, token]);

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) return;
    setIsSubmitting(true);
    setMessage("");
    const form = new FormData(event.currentTarget);
    const result = await login(String(form.get("email") ?? "").trim(), String(form.get("password") ?? ""));
    if (!result.success) {
      setMessage(result.error ?? "Sign-in failed. Please try again.");
      setIsSubmitting(false);
      return;
    }
    router.replace(destination);
  }

  return (
    <main className="login">
      <section className="brand">
        <p className="eyebrow">PROCESS MONITORING · SIMULATION · ADVISORY</p>
        <h1>ProcessTwin</h1>
        <p>Read-only process intelligence. ProcessTwin observes, simulates and advises; it never sends commands to a plant.</p>
      </section>
      <form className="login-card" onSubmit={handleLogin} aria-busy={isSubmitting}>
        <h2>Sign in</h2>
        <label>
          Email
          <input name="email" type="email" autoComplete="username" required autoFocus />
        </label>
        <label>
          Password
          <input name="password" type="password" autoComplete="current-password" required />
        </label>
        <button type="submit" disabled={isSubmitting}>{isSubmitting ? "Signing in…" : "Sign in"}</button>
        {message ? <small role="alert" className="form-error">{message}</small> : null}
      </form>
    </main>
  );
}

export default function Login() {
  return (
    <Suspense fallback={<div className="loading-shell" role="status">Loading…</div>}>
      <LoginForm />
    </Suspense>
  );
}
