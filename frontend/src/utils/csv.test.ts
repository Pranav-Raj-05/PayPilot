import { afterEach, describe, expect, it, vi } from "vitest";
import { downloadCsv } from "./csv";

/** Capture what would be downloaded: the Blob's text and the file name given to the temporary link. */
function capture() {
  let blob!: Blob;
  const create = vi.fn((b: Blob) => { blob = b; return "blob:fake"; });
  const revoke = vi.fn();
  Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
  let download = "";
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { download = this.download; });
  // jsdom's Blob has no .text()
  const read = () => new Promise<string>((res) => { const r = new FileReader(); r.onload = () => res(r.result as string); r.readAsText(blob); });
  return { text: read, download: () => download, revoke };
}

afterEach(() => vi.restoreAllMocks());

describe("downloadCsv", () => {
  it("writes rows as comma-separated lines and names the file", async () => {
    const c = capture();
    downloadCsv("links.csv", [["Description", "Amount"], ["Consulting", 120]]);
    expect(await c.text()).toBe("Description,Amount\nConsulting,120");
    expect(c.download()).toBe("links.csv");
  });

  it("quotes values containing commas, quotes or newlines and doubles embedded quotes", async () => {
    const c = capture();
    downloadCsv("x.csv", [["Acme, Inc.", 'He said "hi"', "line1\nline2"]]);
    expect(await c.text()).toBe('"Acme, Inc.","He said ""hi""","line1\nline2"');
  });

  it("writes empty cells for null and undefined", async () => {
    const c = capture();
    downloadCsv("x.csv", [["a", null, undefined, 0]]);
    expect(await c.text()).toBe("a,,,0");
  });

  it("releases the temporary object URL", () => {
    const c = capture();
    downloadCsv("x.csv", [["a"]]);
    expect(c.revoke).toHaveBeenCalled();
  });
});
