// P51: the Tip/InfoTip primitive — the app's first real tooltip/popover.
// Hover + focus open it, Escape closes it, aria-describedby wires it to the
// anchor, and rich content renders as a small card (role=dialog).
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InfoTip, Tip } from "../components/ui";

describe("Tip", () => {
  it("opens on hover, closes on unhover, and wires aria-describedby", async () => {
    render(<Tip content="Plain explanation"><span>anchor</span></Tip>);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    await userEvent.hover(screen.getByText("anchor"));
    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent("Plain explanation");
    expect(screen.getByText("anchor").parentElement).toHaveAttribute("aria-describedby", tip.id);

    await userEvent.unhover(screen.getByText("anchor"));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("closes on Escape and renders rich content as a card", async () => {
    render(
      <Tip content={<div>Rich <strong>card</strong></div>} label="Score math" interactive>
        <span>42</span>
      </Tip>
    );
    await userEvent.hover(screen.getByText("42"));
    expect(await screen.findByRole("dialog", { name: "Score math" })).toHaveTextContent("Rich card");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("InfoTip is focusable and opens on focus", async () => {
    render(<InfoTip content="What this word means" label="More about caps" />);
    await userEvent.tab();
    expect(await screen.findByRole("tooltip")).toHaveTextContent("What this word means");
  });
});
