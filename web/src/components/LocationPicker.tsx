// Where is this job? (the global rollout, 2026-09-12)
//
// The form asks for a COUNTRY. Which set of hiring rules applies is a lookup the
// app does, not something a recruiter in Budapest should have to know — and the
// old picker asked the other way round, listing packs ("European Union") with
// India preselected, so an untouched dropdown created an India requisition.
//
// Three answers, and only the ones that matter are shown: the country always; the
// state when the country is the United States, because the law there is a state
// question; and "is this job in New York City?" only for New York, because Local
// Law 144 is a city ordinance.
//
// NOTHING IS PRESELECTED. A blank is the honest starting state — the app does not
// know where the job is until someone says.
import { Field, Select } from "./ui";
import type { LocationsFeed } from "../types";

export interface LocationChoice {
  country: string | null;
  us_state: string | null;
  nyc: boolean;
}

export const EMPTY_LOCATION: LocationChoice = { country: null, us_state: null, nyc: false };

/** The pack a choice resolves to, or null when the choice is incomplete or the
 *  country has no rules yet. The server checks this again — this is so the form
 *  can say what will happen before anyone presses a button. */
export function packFor(feed: LocationsFeed | undefined, choice: LocationChoice): string | null {
  if (!feed || !choice.country) return null;
  const country = feed.countries.find((c) => c.code === choice.country);
  if (!country) return null;
  if (country.code !== "US") return country.pack;
  if (choice.us_state === feed.nyc_state && choice.nyc) return feed.nyc_pack;
  const state = feed.us_states.find((s) => s.code === choice.us_state);
  return state ? state.pack : country.pack;
}

/** Why the form can't continue yet, in the recruiter's words — or null when it can. */
export function blockingReason(feed: LocationsFeed | undefined,
                               choice: LocationChoice): string | null {
  if (!feed) return null;                       // still loading; not a refusal
  if (!choice.country) return "Choose the country this job is in.";
  const country = feed.countries.find((c) => c.code === choice.country);
  if (country && country.pack === null) {
    return `There are no hiring rules for ${country.name} yet — ask HR and Legal to add them before screening a requisition there.`;
  }
  if (choice.country === "US" && !choice.us_state) {
    return "Choose the state — in the United States the rules differ by state.";
  }
  return null;
}

export default function LocationPicker({ feed, value, onChange, disabled }: {
  feed: LocationsFeed | undefined;
  value: LocationChoice;
  onChange: (next: LocationChoice) => void;
  disabled?: boolean;
}) {
  const countries = feed?.countries ?? [];
  const states = feed?.us_states ?? [];
  const isUs = value.country === "US";
  const isNy = isUs && value.us_state === (feed?.nyc_state ?? "NY");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Field label="Country" hint="sets the hiring rules & how long records are kept">
        <Select
          value={value.country ?? ""}
          disabled={disabled}
          aria-label="Country"
          onChange={(e) => onChange({
            country: e.target.value || null,
            us_state: e.target.value === "US" ? value.us_state : null,
            nyc: false,
          })}
        >
          <option value="">— Choose a country —</option>
          {countries.map((c) => (
            <option key={c.code} value={c.code}>
              {c.name}{c.pack === null ? " (no hiring rules yet)" : ""}
            </option>
          ))}
        </Select>
      </Field>

      {isUs && (
        <Field label="State" hint="US hiring rules differ by state">
          <Select
            value={value.us_state ?? ""}
            disabled={disabled}
            aria-label="State"
            onChange={(e) => onChange({ ...value, us_state: e.target.value || null, nyc: false })}
          >
            <option value="">— Choose a state —</option>
            {states.map((s) => (
              <option key={s.code} value={s.code}>{s.name}</option>
            ))}
          </Select>
        </Field>
      )}

      {isNy && (
        <label style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 13.5 }}>
          <input
            type="checkbox"
            checked={value.nyc}
            disabled={disabled}
            onChange={(e) => onChange({ ...value, nyc: e.target.checked })}
          />
          <span>
            This job is in New York City
            <span style={{ color: "var(--ink-soft)" }}> — the city has its own hiring law</span>
          </span>
        </label>
      )}
    </div>
  );
}
