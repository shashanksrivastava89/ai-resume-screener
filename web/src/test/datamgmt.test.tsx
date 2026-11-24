// P47: the Data page (storage map + typed-confirm deletes). The factor-change
// flow moved into the Scoring model page (scoring.test.tsx) with the merge.
import { describe, it, expect, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DataPage from "../pages/DataPage";
import { mockApi, renderPage, resetApiMock } from "./utils";

const SUMMARY = {
  data_root: "/data",
  rows: [
    { kind: "Secrets (API keys)", path: "Azure Key Vault", format: "—", note: "In the vault", size: "—" },
    { kind: "Cache", path: "/data/.cache", format: "JSON by hash", note: "Safe to delete", size: "12 KB" },
  ],
};


beforeEach(() => resetApiMock());

describe("Data page", () => {
  it("shows the storage map and deletes the cache only after the phrase is typed", async () => {
    const api = mockApi({
      "/api/data/summary": SUMMARY,
      "/api/data/delete-cache": { ok: true, message: "Cache deleted. The next screening rebuilds it — nothing else changed." },
    });
    renderPage(<DataPage />, { route: "/data" });

    expect(await screen.findByText("Cache")).toBeInTheDocument();
    expect(screen.getByText("/data/.cache")).toBeInTheDocument();
    // the audit-trail note is present
    expect(screen.getByText(/it is the audit trail/)).toBeInTheDocument();

    const btn = screen.getByRole("button", { name: "Delete cache" });
    expect(btn).toBeDisabled(); // gated until the phrase matches

    await userEvent.type(screen.getByPlaceholderText(/Type "delete cache"/), "delete cache");
    expect(btn).toBeEnabled();
    await userEvent.click(btn);

    await waitFor(() => expect(api.callsTo("/api/data/delete-cache")).toHaveLength(1));
    expect(api.callsTo("/api/data/delete-cache")[0].body).toEqual({ confirm: "delete cache" });
    expect(await screen.findByText(/next screening rebuilds it/)).toBeInTheDocument();
  });
});
