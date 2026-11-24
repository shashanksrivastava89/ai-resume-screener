// Test bootstrap (Addendum 11 P30). Runs before every test file.
//
// - Registers @testing-library/jest-dom's matchers against vitest's `expect`
//   (the "/vitest" entrypoint augments vitest's Assertion type too, so
//   toBeInTheDocument()/toHaveTextContent() are typed project-wide with globals
//   still OFF).
// - After each test: unmount React trees and clear the fetch mock's routes +
//   recorded calls, so one test's stubs never leak into the next.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup, configure } from "@testing-library/react";
import { resetApiMock } from "./utils";
import { invalidate } from "../hooks";

// Testing Library keeps its OWN budget for findBy*/waitFor — 1000ms by default,
// entirely separate from vitest's testTimeout. The 2026-08-20 pass raised
// testTimeout to 20s and stopped there, which fixed the "Test timed out" failures
// and left this one untouched: under CPU contention a render that normally takes
// 200ms takes over a second, findBy gives up, and the failure reads "Unable to
// find an element" — a content bug, not a timing one. That is the worse message,
// because it sends whoever reads it looking at the component.
//
// Measured 2026-09-12 on this machine (12 cores, 65 test files): back-to-back
// full runs failed 1, 8, 9 and 1 tests, every one of them a findBy in a Review or
// drawer suite, and every one of them passing in isolation. At 5s the same runs
// are clean. Nothing here legitimately waits seconds, so this buys the same
// reliability the testTimeout change bought without hiding a real hang.
configure({ asyncUtilTimeout: 5_000 });

afterEach(() => {
  cleanup();
  resetApiMock();
  // The useFetch SWR cache (F9) is module-level — clear it so one test's cached
  // responses never bleed into the next.
  invalidate();
  // Draft persistence (F8) writes to localStorage, and the dashboard filter
  // persistence (2026-07-20) to sessionStorage — clear both so one test's
  // drafts/filters never leak into the next.
  try { localStorage.clear(); } catch { /* jsdom always has it, but be safe */ }
  try { sessionStorage.clear(); } catch { /* same */ }
});
