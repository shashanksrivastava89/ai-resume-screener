// The three groups the checklist is organised into (2026-08-20).
//
// KEEP IN SYNC with models.py (CATEGORY_OF_TYPE / CATEGORY_ORDER / CATEGORY_LABELS),
// which is the source of truth: the server already sends a resolved `category` on
// every row it returns, so this mirror exists only for rows the CLIENT creates before
// they have ever been saved (the add-a-requirement row, and the new-requisition
// wizard, which has no requisition to POST to yet).
//
// `type` remains the stored contract — the category is derived from it — so no rubric
// YAML on disk needs migrating and an old checklist groups correctly the first time
// it is opened.

import type { ReactNode } from "react";

export type Category = "skills" | "experience" | "qualifications" | "other";

export const CATEGORY_OF_TYPE: Record<string, Category> = {
  skill: "skills",
  language: "skills",          // a working language IS a skill to a screener
  experience: "experience",
  achievement: "experience",   // "exceeded quota" is evidence OF experience
  domain: "experience",        // sector exposure is experience, not a credential
  education: "qualifications",
  certification: "qualifications",
};

export function categoryOf(type?: string | null): Category {
  return CATEGORY_OF_TYPE[(type ?? "").trim().toLowerCase()] ?? "other";
}

/** The `type` a row gets when it is added straight into a group, so a new row files
 *  itself under the heading the recruiter added it beneath. */
export const DEFAULT_TYPE_FOR: Record<Category, string> = {
  skills: "skill",
  experience: "experience",
  qualifications: "certification",
  other: "other",
};

export interface CategorySpec {
  key: Category;
  label: string;
  /** What belongs here, in recruiter language — shown under the heading when the
   *  group is empty, so an unfamiliar user knows what to type. */
  hint: string;
  placeholder: string;
}

// Display order. "other" is last and is only rendered when it has rows: an
// unclassifiable requirement must never be silently dropped from the checklist
// (guardrail §6.3 — no hiding), but it should not invent a fourth heading either.
export const CATEGORIES: CategorySpec[] = [
  {
    key: "skills",
    label: "Skills",
    hint: "Tools, techniques and abilities the job needs — and any working language.",
    placeholder: "e.g. Ind AS reporting, SAP FICO, channel negotiation…",
  },
  {
    key: "experience",
    label: "Experience",
    hint: "Time spent doing the work, the sectors it was done in, and what it produced.",
    placeholder: "e.g. 5+ years in listed-company finance, led a statutory audit…",
  },
  {
    key: "qualifications",
    label: "Qualifications",
    hint: "Degrees, professional qualifications and certifications, including licences.",
    placeholder: "e.g. CA qualified, MBA Finance, NISM Series-V A…",
  },
  {
    key: "other",
    label: "Other",
    hint: "Requirements that do not fit the three groups above.",
    placeholder: "Describe the requirement in plain language…",
  },
];

export const CATEGORY_LABEL: Record<Category, string> = {
  skills: "Skills",
  experience: "Experience",
  qualifications: "Qualifications",
  other: "Other",
};

/** Icon per group — assigned by the caller to avoid importing JSX here. */
export type CategoryIcons = Record<Category, ReactNode>;
