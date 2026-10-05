"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../../components/auth-provider";

export default function Login() {
  const { login, isLoading } = useAuth();
  const router = useRouter();
  const [message, setMessage] = useState("Sign in with the documented demo account to inspect simulated CSTR R-101 data.");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) return;

    setIsSubmitting(true);
    setMessage("Signing in...");

    const form = new FormData(event.currentTarget);
    const email = form.get("email") as string;
    const password = form.get("password") as string;

    const result = await login(email, password);

    if (result.success) {
      setMessage("Connected to the simulated demonstration tenant.");
      router.push("/dashboard");
      router.refresh();
    } else {
      setMessage(result.error ?? "Sign-in failed. Confirm that demo data has been seeded.");
      setIsSubmitting(false);
    }
  }

  if (isLoading) {
    return (
      <main className="login-loading">
        <section className="brand">
          <p className="eyebrow">PROCESS TWIN / REFERENCE PLANT</p>
          <h1>Physics-informed<br />industrial intelligence.</h1>
          <p>Monitor, simulate and optimize—without sending control commands to the plant.</p>
        </section>
        <div className="loading-spinner">Loading...</div>
      </main>
    );
  }

  return (
    <main className="login">
      <section className="brand">
        <p className="eyebrow">PROCESS TWIN / REFERENCE PLANT</p>
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