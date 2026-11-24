// Bulk uploads are posted in bounded groups (2026-08-26).
//
// A 70 MB / 6-packet iCIMS Bulk Print batch posted as ONE request ran past the ~230s
// gateway timeout and returned 504 with the split half finished. The endpoint is
// incremental and idempotent, so the fix is to bound the work per request rather than
// to make the server faster than physics.
import { describe, it, expect } from "vitest";
import { chunkForUpload } from "../api";

/** File.size is read-only in jsdom, so define it — the chunker only reads .size. */
function sized(name: string, mb: number): File {
  const f = new File([new Uint8Array(1)], name, { type: "application/pdf" });
  Object.defineProperty(f, "size", { value: Math.round(mb * 1024 * 1024) });
  return f;
}

describe("upload chunking", () => {
  it("splits a real bulk-print batch into several groups", () => {
    // The exact batch that 504'd: 6 packets, 70.2 MB.
    const files = [14.4, 12.3, 11.1, 3.2, 16.9, 12.3].map((mb, i) => sized(`p${i}.pdf`, mb));
    const chunks = chunkForUpload(files);
    expect(chunks.length).toBeGreaterThan(1);
    // every group is within the byte budget unless it holds a single oversized file
    for (const c of chunks) {
      const bytes = c.reduce((s, f) => s + f.size, 0);
      expect(c.length === 1 || bytes <= 8 * 1024 * 1024).toBe(true);
    }
    // nothing lost, order preserved
    expect(chunks.flat().map((f) => f.name)).toEqual(files.map((f) => f.name));
  });

  it("keeps a small ordinary upload as a single request", () => {
    const files = [0.2, 0.3, 0.25].map((mb, i) => sized(`r${i}.pdf`, mb));
    expect(chunkForUpload(files)).toHaveLength(1);
  });

  it("caps by FILE COUNT too, so hundreds of small résumés aren't one giant body", () => {
    const files = Array.from({ length: 47 }, (_, i) => sized(`r${i}.pdf`, 0.05));
    const chunks = chunkForUpload(files);
    expect(chunks.length).toBe(3);            // 20 + 20 + 7
    expect(chunks.every((c) => c.length <= 20)).toBe(true);
    expect(chunks.flat()).toHaveLength(47);
  });

  it("puts a single over-budget file in its own group rather than dropping it", () => {
    const files = [sized("huge.pdf", 30), sized("small.pdf", 0.1)];
    const chunks = chunkForUpload(files);
    expect(chunks[0]).toHaveLength(1);
    expect(chunks[0][0].name).toBe("huge.pdf");
    expect(chunks.flat()).toHaveLength(2);
  });

  it("handles an empty selection", () => {
    expect(chunkForUpload([])).toEqual([]);
  });
});
