// Human display names for emails (2026-07-20), the client mirror of the server's
// names.py. Dashboards and columns show a NAME with the raw email on hover; the
// server already resolves names in richer contexts (notifications, comments).
export function humanizeEmail(email: string | null | undefined): string {
  const raw = String(email ?? "");
  const local = raw.split("@", 1)[0];
  const parts = local.split(/[._\-+]+/).filter(Boolean);
  if (parts.length === 0) return raw.trim();
  return parts.map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join(" ");
}
