import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation, type MemoryRouterProps } from "react-router-dom";
import { ToastProvider } from "@/context/ToastContext";

/** Shows where the router is, so tests can assert navigation. */
export function LocationProbe() {
  const l = useLocation();
  return <div data-testid="location">{l.pathname}{l.search}</div>;
}

export type InitialEntry = NonNullable<MemoryRouterProps["initialEntries"]>[number];

interface Options { route?: InitialEntry; path?: string; extraRoutes?: ReactNode }

/**
 * Renders `ui` at `path` (default: any) inside the providers the real app has (React Query with retries off, router, toasts).
 * Navigation to any other path lands on a probe route that prints the location.
 */
export function renderPage(ui: ReactNode, { route = "/", path = "*", extraRoutes }: Options = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  const user = userEvent.setup();
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <ToastProvider>
          <Routes>
            <Route path={path} element={ui} />
            {extraRoutes}
            <Route path="*" element={<LocationProbe />} />
          </Routes>
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { user, client, ...result };
}
