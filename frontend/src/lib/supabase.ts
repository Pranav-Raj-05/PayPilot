import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const key = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

export const isSupabaseConfigured = Boolean(url && key);
// Only the public anon key is ever used in the browser. The service-role key never leaves the backend.
export const supabase = createClient(url || "http://localhost:54321", key || "anon-key-not-configured", {
  auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
});
