// The unsaved-changes guard (2026-09-06): a dirty editor gets to object before an
// in-app navigation; a clean one never sees the dialog.
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LeaveGuardProvider, pendingLeaveMessage, useConfirmLeave, useLeaveGuard } from "../leave";
import { ToastProvider } from "../components/overlays";

function Editor({ dirty }: { dirty: boolean }) {
  useLeaveGuard(dirty ? "This checklist has edits that are not published yet." : null);
  return <div>editor</div>;
}

function Nav({ go }: { go: () => void }) {
  const confirmLeave = useConfirmLeave();
  return <button onClick={() => confirmLeave(go)}>leave</button>;
}

function Harness({ dirty, go }: { dirty: boolean; go: () => void }) {
  return (
    <ToastProvider>
      <LeaveGuardProvider>
        <Editor dirty={dirty} />
        <Nav go={go} />
      </LeaveGuardProvider>
    </ToastProvider>
  );
}

describe("leave guard", () => {
  it("navigates at once when nothing is dirty", async () => {
    const go = vi.fn();
    render(<Harness dirty={false} go={go} />);
    expect(pendingLeaveMessage()).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "leave" }));
    expect(go).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("asks first when an editor is dirty, and proceeds only on Leave anyway", async () => {
    const go = vi.fn();
    render(<Harness dirty={true} go={go} />);
    expect(pendingLeaveMessage()).toMatch(/not published yet/);
    await userEvent.click(screen.getByRole("button", { name: "leave" }));
    const dialog = await screen.findByRole("dialog", { name: "Leave without publishing?" });
    expect(dialog).toHaveTextContent(/saved as a draft on this browser/);
    expect(go).not.toHaveBeenCalled();
    // staying keeps the editor; leaving runs the navigation exactly once
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(go).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "leave" }));
    await userEvent.click(await screen.findByRole("button", { name: "Leave anyway" }));
    expect(go).toHaveBeenCalledTimes(1);
  });

  it("unregisters when the editor unmounts", () => {
    const { unmount } = render(<Harness dirty={true} go={() => {}} />);
    expect(pendingLeaveMessage()).not.toBeNull();
    unmount();
    expect(pendingLeaveMessage()).toBeNull();
  });
});
