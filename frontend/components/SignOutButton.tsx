"use client";

import { useRouter } from "next/navigation";
import { API_BASE_URL } from "@/lib/api";

export function SignOutButton() {
  const router = useRouter();

  async function signOut() {
    await fetch(`${API_BASE_URL}/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
    router.push("/login");
    router.refresh();
  }

  return (
    <button className="secondary-button" type="button" onClick={signOut}>
      Sign out
    </button>
  );
}
