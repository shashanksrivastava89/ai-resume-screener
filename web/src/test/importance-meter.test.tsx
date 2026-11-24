// The importance meter reads as a control (2026-09-06, pilot feedback item 1): a
// caption, a live label, hover PREVIEWS the level before it is chosen, and the
// bars take the row's kind colour through the .req-card ancestor's CSS variables.
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ImportanceMeter } from "../components/ui";

describe("<ImportanceMeter>", () => {
  it("labels the level, previews on hover and commits on click", async () => {
    const onChange = vi.fn();
    render(
      <div className="req-card kind-must_have">
        <ImportanceMeter value={2} onChange={onChange} />
      </div>
    );
    const meter = screen.getByRole("group", { name: "Importance: Low–med" });
    expect(meter).toHaveTextContent("Importance");
    expect(meter).toHaveTextContent("Low–med");
    expect(meter.getAttribute("title")).toContain("Click a bar to change");
    const bars = [1, 2, 3, 4, 5].map((n) => screen.getByRole("button", { name: `Importance ${n} of 5` }));
    expect(bars[1].className).toContain("is-on");
    expect(bars[2].className).not.toContain("is-on");

    // hovering the 4th bar previews "High" without changing the value
    await userEvent.hover(bars[3]);
    expect(meter).toHaveTextContent("High");
    expect(bars[2].className).toContain("is-preview");
    expect(bars[3].className).toContain("is-preview");
    expect(onChange).not.toHaveBeenCalled();
    await userEvent.unhover(bars[3]);
    expect(meter).toHaveTextContent("Low–med");

    await userEvent.click(bars[3]);
    expect(onChange).toHaveBeenCalledWith(4);
  });

  it("is inert without an onChange (read-only surfaces)", () => {
    render(<ImportanceMeter value={5} />);
    const meter = screen.getByRole("group", { name: "Importance: Critical" });
    expect(meter.className).not.toContain("is-editable");
    expect(screen.getByRole("button", { name: "Importance 1 of 5" })).toBeDisabled();
  });
});
