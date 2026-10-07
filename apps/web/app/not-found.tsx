import Link from "next/link";

export default function NotFound() {
  return (
    <main className="not-found-page">
      <p className="eyebrow">404 · PAGE NOT FOUND</p>
      <h1>This page does not exist</h1>
      <p>The address may have been mistyped or the view was retired.</p>
      <Link href="/dashboard">Return to the dashboard</Link>
    </main>
  );
}
