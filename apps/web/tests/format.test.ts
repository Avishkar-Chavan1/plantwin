import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ApiError,
  AUTH_REQUEST_TIMEOUT_MS,
  describeFailure,
  LOGIN_REQUEST_TIMEOUT_MS,
  readErrorMessage,
  safeRedirectTarget,
  timeoutSignal,
} from "../components/api-client.ts";
import { formatAge, formatNumber, modeInfo, normalizeMode } from "../components/format.ts";

test("safeRedirectTarget only accepts same-origin relative application paths", () => {
  assert.equal(safeRedirectTarget("/plants"), "/plants");
  assert.equal(safeRedirectTarget("/data?tab=1"), "/data?tab=1");
  for (const hostile of ["//evil.example", "/\\evil.example", "https://evil.example", "javascript:alert(1)", "dashboard", "/ok\r\nSet-Cookie: x=1"]) {
    assert.equal(safeRedirectTarget(hostile), "/dashboard", hostile);
  }
  assert.equal(safeRedirectTarget("/login?redirect=/login"), "/dashboard");
  assert.equal(safeRedirectTarget(null), "/dashboard");
  assert.equal(safeRedirectTarget(undefined, "/alerts"), "/alerts");
});

test("normalizeMode never invents a data mode", () => {
  assert.equal(normalizeMode("LIVE_READ_ONLY"), "LIVE_READ_ONLY");
  assert.equal(normalizeMode("SIMULATION"), "SIMULATION");
  for (const unknown of [undefined, null, 3, "", "live", "__proto__", "constructor", "toString"]) {
    assert.equal(normalizeMode(unknown), "NO_DATA", String(unknown));
  }
  assert.match(modeInfo("SIMULATION").label, /SIMULATION/);
  assert.match(modeInfo("LIVE_READ_ONLY").label, /READ-ONLY/);
});

test("formatters show an em dash rather than a made-up number", () => {
  assert.equal(formatNumber(null), "—");
  assert.equal(formatNumber(undefined), "—");
  assert.equal(formatNumber(Number.NaN), "—");
  assert.equal(formatNumber(Number.POSITIVE_INFINITY), "—");
  assert.equal(formatNumber(12.3456, 1), "12.3");
  assert.equal(formatAge(null), "—");
  assert.equal(formatAge(12), "12 s ago");
  assert.equal(formatAge(600), "10 min ago");
  assert.equal(formatAge(7200), "2.0 h ago");
  assert.equal(formatAge(3 * 86_400), "3 d ago");
});

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

test("readErrorMessage surfaces the API message and a short request reference", async () => {
  const message = await readErrorMessage(
    jsonResponse(422, { error: { code: "INVALID_REQUEST", message: "Request validation failed - plant_tag: required", request_id: "abcdef0123456789" } }),
    "fallback",
  );
  assert.equal(message, "Request validation failed - plant_tag: required (reference abcdef01)");
  assert.equal(await readErrorMessage(jsonResponse(400, { detail: "X-Organization-ID header is required" }), "f"), "X-Organization-ID header is required");
  assert.equal(await readErrorMessage(jsonResponse(409, { detail: { code: "X", message: "Already exists" } }), "f"), "Already exists");
});

test("readErrorMessage gives actionable text for bodies without a message", async () => {
  assert.match(await readErrorMessage(new Response("<html>bad gateway</html>", { status: 502 }), "x"), /temporarily unavailable/);
  assert.match(await readErrorMessage(new Response("", { status: 401 }), "x"), /Sign in again/);
  assert.match(await readErrorMessage(new Response("", { status: 403 }), "x"), /role/);
  assert.match(await readErrorMessage(new Response("", { status: 413 }), "x"), /larger/);
  assert.match(await readErrorMessage(new Response("", { status: 429 }), "x"), /Too many/);
  assert.match(await readErrorMessage(new Response("", { status: 500 }), "Load failed."), /Load failed\. \(HTTP 500\)/);
});

test("network and timeout failures are described distinctly", () => {
  assert.match(describeFailure(new DOMException("t", "TimeoutError"), 30_000), /timed out after 30 s/);
  assert.match(describeFailure(new TypeError("Failed to fetch"), 30_000), /Cannot reach the ProcessTwin API/);
  assert.match(describeFailure(new Error("storage unavailable"), 30_000), /could not be completed/);
});

test("login has a bounded timeout longer than background auth requests", async () => {
  assert.equal(LOGIN_REQUEST_TIMEOUT_MS, 30_000);
  assert.equal(AUTH_REQUEST_TIMEOUT_MS, 15_000);

  const signal = timeoutSignal(10);
  await new Promise<void>((resolve) => signal.addEventListener("abort", () => resolve(), { once: true }));
  assert.equal(signal.aborted, true);
  assert.equal(signal.reason.name, "TimeoutError");
});

test("ApiError carries status, code and request id", () => {
  const error = new ApiError(0, "offline", "NETWORK", "r1");
  assert.equal(error.status, 0);
  assert.equal(error.code, "NETWORK");
  assert.equal(error.requestId, "r1");
  assert.ok(error instanceof Error);
});