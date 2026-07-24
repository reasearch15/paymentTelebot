import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE_NAME = process.env.SESSION_COOKIE_NAME ?? "payment_ledger_session";

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const isLogin = pathname === "/login";
  const hasSession = request.cookies.has(SESSION_COOKIE_NAME);

  if (isLogin && hasSession) {
    return NextResponse.redirect(new URL("/dashboard", request.url));
  }

  if (!isLogin && !hasSession) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
