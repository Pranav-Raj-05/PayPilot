import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// jsdom lacks these browser APIs that the app (and recharts) touch.
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
vi.stubGlobal("ResizeObserver", ResizeObserverStub);
Element.prototype.scrollIntoView = vi.fn();

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  try { localStorage.clear(); } catch { /* storage unavailable in this runtime */ }
});
