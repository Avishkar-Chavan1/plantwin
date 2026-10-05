"use client";

import { useState } from "react";

interface NotImplementedProps {
  title: string;
  description: string;
  icon?: string;
}

export function NotImplemented({ title, description, icon = "🚧" }: NotImplementedProps) {
  const [copied, setCopied] = useState(false);

  return (
    <main className="not-implemented">
      <section className="brand">
        <p className="eyebrow">PROCESS TWIN / REFERENCE PLANT</p>
        <h1>Physics-informed<br />industrial intelligence.</h1>
        <p>Monitor, simulate and optimize—without sending control commands to the plant.</p>
      </section>
      <article className="not-implemented-card">
        <span className="icon" aria-hidden="true">{icon}</span>
        <h2>{title}</h2>
        <p>{description}</p>
        <p className="hint">This page is not yet implemented. The backend API endpoints may exist but the frontend view is pending.</p>
      </article>
    </main>
  );
}