import { createContext, useContext, useState, type ReactNode } from "react";

const Ctx = createContext<{ days: number; setDays: (d: number) => void }>({ days: 30, setDays: () => {} });
export const RANGES = [{ value: 7, label: "7D", long: "Last 7 Days" }, { value: 30, label: "30D", long: "Last 30 Days" }, { value: 90, label: "90D", long: "Last 90 Days" }, { value: 365, label: "12M", long: "Last 12 Months" }];

export function RangeProvider({ children }: { children: ReactNode }) {
  const [days, setDaysState] = useState(() => { try { return Number(localStorage.getItem("pp.days")) || 30; } catch { return 30; } });
  const setDays = (d: number) => { setDaysState(d); try { localStorage.setItem("pp.days", String(d)); } catch { /* storage unavailable */ } };
  return <Ctx.Provider value={{ days, setDays }}>{children}</Ctx.Provider>;
}
export const useRange = () => useContext(Ctx);
