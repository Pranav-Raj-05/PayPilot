import axios, { AxiosError } from "axios";
import { supabase } from "./supabase";

export class ApiError extends Error {
  constructor(message: string, public status = 0, public code = "error") {
    super(message);
  }
}

export const api = axios.create({ baseURL: (import.meta.env.VITE_API_URL as string) || "http://localhost:8000", timeout: 60000 });

api.interceptors.request.use(async (config) => {
  const { data } = await supabase.auth.getSession();
  if (data.session?.access_token) config.headers.Authorization = `Bearer ${data.session.access_token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err: AxiosError<any>) => {
    const status = err.response?.status ?? 0;
    const body = err.response?.data?.error;
    if (status === 401) void supabase.auth.signOut();
    const message = body?.message || (status === 0 ? "Cannot reach the PayPilot API. Check your connection." : "Something went wrong");
    return Promise.reject(new ApiError(message, status, body?.code));
  },
);

export const get = async <T,>(url: string, params?: object) => (await api.get<T>(url, { params })).data;
export const post = async <T,>(url: string, body?: object, params?: object) => (await api.post<T>(url, body ?? {}, { params })).data;
export const patch = async <T,>(url: string, body?: object) => (await api.patch<T>(url, body ?? {})).data;
export const del = async (url: string) => { await api.delete(url); };
// Public (customer-facing) calls need no auth header.
export const publicApi = axios.create({ baseURL: api.defaults.baseURL, timeout: 30000 });
