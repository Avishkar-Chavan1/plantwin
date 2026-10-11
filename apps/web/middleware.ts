import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Navigation gate only. The marker cookie is written by the client after a successful sign-in
 * and carries no credential; it merely lets us send an unauthenticated visitor to /login before
 * any authenticated page shell is served. Authorization is always enforced by the API, and the
 * client-side AuthGuard validates the real session (token + /auth/me) before rendering data.
 *
 * `/` is the public marketing landing page: anonymous visitors may read it, while a visitor
 * who already holds the session marker is sent straight into the workspace.
 */
const SESSION_MARKER = "processtwin-session";

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const hasSession = request.cookies.get(SESSION_MARKER)?.value === "1";

  if (pathname === "/") {
    if (!hasSession) return NextResponse.next();
    const dashboard = request.nextUrl.clone();
    dashboard.pathname = "/dashboard";
    dashboard.search = "";
    return NextResponse.redirect(dashboard);
  }

  if (hasSession) return NextResponse.next();
  const loginUrl = request.nextUrl.clone();
  loginUrl.pathname = "/login";
  loginUrl.search = "";
  loginUrl.searchParams.set("redirect", `${pathname}${search}`);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  // Everything except the login page, Next internals and static assets.
  matcher: ["/((?!login|_next/static|_next/image|favicon.ico|robots.txt|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)"],
};
