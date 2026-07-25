import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE_NAME = process.env.SESSION_COOKIE_NAME ?? "payment_ledger_session";

function publicUrl(request: NextRequest, pathname: string): URL {
  const host =
    request.headers.get("x-forwarded-host") ??
    request.headers.get("host") ??
    "payment.youplatform.org";

  const protocol =
    request.headers.get("x-forwarded-proto") ??
    (host.includes("localhost") ? "http" : "https");

  return new URL(pathname, `${protocol}://${host}`);
}

export function middleware(request: NextRequest) {
  const pathname = request.nextUrl.pathname;
  const isLogin = pathname === "/login";
  const hasSession = request.cookies.has(SESSION_COOKIE_NAME);

  if (isLogin && hasSession) {
    return NextResponse.redirect(publicUrl(request, "/dashboard"));
  }

  if (!isLogin && !hasSession) {
    return NextResponse.redirect(publicUrl(request, "/login"));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/",
    "/dashboard/:path*",
    "/emails/:path*",
    "/integrations/:path*",
    "/ledger/:path*",
    "/settlements/:path*",
    "/player-ledger/:path*",
    "/player-settlements/:path*",
    "/login",
  ],
};
