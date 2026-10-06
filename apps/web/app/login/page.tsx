"use client";

import { FormEvent, useState, useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/components/auth-provider";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { login } = useAuth();
  const [message, setMessage] = useState("Sign in to access your organization’s read-only process intelligence workspace.");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [redirectTo, setRedirectTo] = useState("/dashboard");

  // Get redirect from search params (set by middleware) - use useEffect to avoid render-phase state update
  useEffect(() => {
    const redirectParam = searchParams?.get("redirect");
    if (redirectParam) {
      setRedirectTo(redirectParam);
    }
  }, [searchParams]);

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) return;

    setIsSubmitting(true);
    setMessage("Signing in...");

    const form = new FormData(event.currentTarget);
    const email = form.get("email") as string;
    const password = form.get("password") as string;

    const result = await login(email, password);
    if (!result.success) {
      setMessage(result.error ?? "Sign-in failed. Please try again.");
      setIsSubmitting(false);
      return;
    }
    router.replace(redirectTo.startsWith("/") ? redirectTo : "/dashboard");
    router.refresh();
  }

  return (
    <main className="login">
      <section className="brand">
        <p className="eyebrow">PROCESS INTELLIGENCE PLATFORM</p>
        <h1>Physics-informed<br />industrial intelligence.</h1>
        <p>Monitor, simulate and optimize—without sending control commands to the plant.</p>
      </section>
      <form className="login-card" onSubmit={handleLogin}>
        <h2>Welcome back</h2>
        <label>
          Email
          <input name="email" type="email" defaultValue="engineer@processtwin.demo" required />
        </label>
        <label>
          Password
          <input name="password" type="password" defaultValue="demo-password-123!" required />
        </label>
        <button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Signing in..." : "Sign in"}
        </button>
        <small>{message}</small>
      </form>
    </main>
  );
}

export default function Login() {
  return (
    <Suspense fallback={<div className="login"><div>Loading...</div></div>}>
      <LoginForm />
    </Suspense>
  );
}
