// Language codes -> the name a recruiter reads (global rollout, 2026-09-12).
//
// The sidecar used to store only the flag "non_english", so every requisition
// whose JD was not in English wore the same badge — and a Hungarian JD wore no
// badge at all, because the detector counted non-Latin characters and Hungarian
// is written in Latin ones. It stores an ISO 639-1 code now.
//
// Mirrors language.LANGUAGE_NAMES on the server. Kept small on purpose: an
// unknown code renders as itself rather than as nothing, so a language we add
// server-side is legible here before this file catches up.
const LANGUAGE_NAMES: Record<string, string> = {
  hu: "Hungarian",
  de: "German",
  fr: "French",
  es: "Spanish",
  it: "Italian",
  nl: "Dutch",
  pl: "Polish",
  pt: "Portuguese",
  ro: "Romanian",
  cs: "Czech",
  en: "English",
  non_english: "non-English",
};

export function languageName(code: string | null | undefined): string {
  if (!code) return "";
  return LANGUAGE_NAMES[code] ?? code;
}

/** The badge text for a requisition's JD language, or null when there is nothing
 *  worth saying (English, or a language we could not place). */
export function jdLanguageBadge(code: string | null | undefined): string | null {
  if (!code || code === "en") return null;
  return `${languageName(code)} JD`;
}
