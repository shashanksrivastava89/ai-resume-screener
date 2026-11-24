// Browser-local draft persistence so a long editing session survives an
// accidental navigation, refresh or browser close. Drafts are UNSAVED editor
// state only — publishing stays an explicit, separate action, and a draft is
// cleared on a successful publish. Everything is best-effort: quota / private
// mode / disabled storage all degrade to a silent no-op, and a corrupt entry is
// removed on read. Keys follow the rs.* convention (see RegionContext).
import type { Requirement } from "./types";

const ck = (reqId: string) => `rs.checklistDraft.${reqId}`;
const WIZARD_KEY = "rs.wizardDraft"; // one in-flight wizard at a time

export interface ChecklistDraft {
  savedAt: string;
  baseVersion: number | null; // the published version the edits started from
  rows: Requirement[];
}

export interface WizardDraft {
  savedAt: string;
  reqId: string;
  details: {
    icims: string; title: string; jd: string; loc: string | null;
    /** Where the job is (2026-09-12). `loc` is the pack this resolves to; a draft
     *  saved before the country picker existed has no `place` and restores with an
     *  empty one, which is the honest state — the app never knew the country. */
    place?: { country: string | null; us_state: string | null; nyc: boolean };
  };
  rows: Requirement[];
  stage: "details" | "checklist";
}

function read<T>(key: string, ok: (v: unknown) => boolean): T | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!ok(parsed)) throw new Error("bad shape");
    return parsed as T;
  } catch {
    try { localStorage.removeItem(key); } catch { /* ignore */ }
    return null;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* quota / disabled storage — drafts are a convenience, never required */
  }
}

function clear(key: string): void {
  try { localStorage.removeItem(key); } catch { /* ignore */ }
}

const isChecklist = (v: unknown): v is ChecklistDraft =>
  !!v && typeof v === "object" && Array.isArray((v as ChecklistDraft).rows);

const isWizard = (v: unknown): v is WizardDraft =>
  !!v && typeof v === "object" && Array.isArray((v as WizardDraft).rows) &&
  typeof (v as WizardDraft).details === "object";

export function loadChecklistDraft(reqId: string): ChecklistDraft | null {
  return read<ChecklistDraft>(ck(reqId), isChecklist);
}
export function saveChecklistDraft(reqId: string, draft: ChecklistDraft): void {
  write(ck(reqId), draft);
}
export function clearChecklistDraft(reqId: string): void {
  clear(ck(reqId));
}

export function loadWizardDraft(): WizardDraft | null {
  return read<WizardDraft>(WIZARD_KEY, isWizard);
}
export function saveWizardDraft(draft: WizardDraft): void {
  write(WIZARD_KEY, draft);
}
export function clearWizardDraft(): void {
  clear(WIZARD_KEY);
}

// Compare rows by their meaningful fields only (ignore ids / critic metadata),
// so an unchanged draft never prompts a "restore" and autosave skips no-ops.
export function rowsEqual(a: Requirement[], b: Requirement[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((r, i) =>
    r.text === b[i].text && r.kind === b[i].kind && r.importance === b[i].importance);
}
