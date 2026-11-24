// Exemplar UI tests (Addendum 11 P30) — prove the harness on STABLE targets:
// the Tabs / Badge / Switch primitives in components/ui.tsx. Rendered through
// renderPage so the house wrapper (MemoryRouter + MeProvider) is exercised too.
import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Badge, Switch, Tabs } from "../components/ui";
import { renderPage } from "./utils";

describe("Tabs", () => {
  const tabs = [
    { key: "overview", label: "Overview" },
    { key: "candidates", label: "Candidates", count: 12 },
  ];

  it("renders labels and the count pill", async () => {
    renderPage(<Tabs tabs={tabs} active="overview" onSelect={() => {}} />);
    expect(await screen.findByRole("tab", { name: /Overview/ })).toBeInTheDocument();
    // the count on the Candidates tab is shown next to its label
    expect(screen.getByRole("tab", { name: /Candidates/ })).toHaveTextContent("12");
  });

  it("marks the active tab and fires onSelect on click", async () => {
    const onSelect = vi.fn();
    renderPage(<Tabs tabs={tabs} active="overview" onSelect={onSelect} />);
    const overview = await screen.findByRole("tab", { name: /Overview/ });
    const candidates = screen.getByRole("tab", { name: /Candidates/ });
    expect(overview).toHaveAttribute("aria-selected", "true");
    expect(candidates).toHaveAttribute("aria-selected", "false");

    await userEvent.click(candidates);
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith("candidates");
  });
});

describe("Badge", () => {
  it("styles a Strong band with the brand green face", async () => {
    renderPage(<Badge band="Strong">Strong match</Badge>);
    const badge = await screen.findByText("Strong match");
    expect(badge.style.background).toContain("brand-green");
    expect(badge.style.color).toContain("white");
  });

  it("gives not-a-fit its own (slate) colour, never the body-text grey", async () => {
    // 2026-09-05: the three bands are three hues — green / amber / slate — so they
    // can be told apart at a glance. Slate, not rose: the tool never rejects anyone,
    // so "Not a fit" must stay neutral in feel while being a colour of its own.
    renderPage(<Badge band="Not a fit">Weak match</Badge>);
    const badge = await screen.findByText("Weak match");
    expect(badge.style.background).toContain("hue-slate-tint");
    expect(badge.style.color).toContain("hue-slate-ink");
  });

  it("falls back to a neutral face for an unknown band", async () => {
    renderPage(<Badge band="Mystery">?</Badge>);
    const badge = await screen.findByText("?");
    expect(badge.style.background).toContain("neutral-150");
    expect(badge.style.color).toContain("ink-soft");
  });
});

describe("Switch", () => {
  it("reflects the checked prop via aria-checked", async () => {
    renderPage(<Switch checked={true} onChange={() => {}} label="Blind review" />);
    const toggle = await screen.findByRole("switch");
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("Blind review")).toBeInTheDocument();
  });

  it("fires onChange with the toggled value", async () => {
    const onChange = vi.fn();
    renderPage(<Switch checked={false} onChange={onChange} />);
    await userEvent.click(await screen.findByRole("switch"));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("does not fire onChange when disabled", async () => {
    const onChange = vi.fn();
    renderPage(<Switch checked={false} onChange={onChange} disabled />);
    await userEvent.click(await screen.findByRole("switch"));
    expect(onChange).not.toHaveBeenCalled();
  });
});
