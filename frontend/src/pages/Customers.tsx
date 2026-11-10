import { Pencil, Plus, Trash2, Users } from "lucide-react";
import { useState } from "react";
import { Avatar, Button, Card, EmptyState, ErrorState, Field, Input, Modal, PageHeader, Skeleton, Textarea } from "@/components/ui";
import { useToast } from "@/context/ToastContext";
import { useCreateCustomer, useCustomers, useDeleteCustomer, useUpdateCustomer } from "@/hooks/queries";
import type { Customer } from "@/types";

export default function Customers() {
  const { toast } = useToast();
  const [search, setSearch] = useState("");
  const { data, isLoading, error, refetch } = useCustomers(search || undefined);
  const create = useCreateCustomer(), update = useUpdateCustomer(), del = useDeleteCustomer();
  const [edit, setEdit] = useState<Partial<Customer> | null>(null);
  const save = async () => {
    if (!edit?.name?.trim()) return;
    const body = { name: edit.name.trim(), email: edit.email || null, phone: edit.phone || null, address: edit.address || null, country: edit.country ? edit.country.toUpperCase() : null };
    try { edit.id ? await update.mutateAsync({ id: edit.id, ...body }) : await create.mutateAsync(body); toast("Customer saved"); setEdit(null); } catch (e) { toast((e as Error).message, "error"); }
  };
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Customers" subtitle="People and businesses you invoice." actions={<Button icon={<Plus className="h-4 w-4" />} onClick={() => setEdit({})}>Add customer</Button>} />
      <Card className="overflow-hidden">
        <div className="p-4"><input aria-label="Search customers" className="input max-w-sm" placeholder="Search name or email…" value={search} onChange={(e) => setSearch(e.target.value)} /></div>
        {error ? <ErrorState error={error} onRetry={() => refetch()} /> : isLoading ? <div className="space-y-2 p-6"><Skeleton className="h-12" /></div> : !data?.items.length ? <EmptyState icon={<Users className="h-6 w-6" />} title="No customers" text="Add a customer, or just ask Copilot to invoice someone by name." action={<Button onClick={() => setEdit({})}>Add customer</Button>} /> : (
          <ul>{data.items.map((c) => (
            <li key={c.id} className="flex items-center justify-between gap-3 border-t border-surface-low px-6 py-3">
              <div className="flex min-w-0 items-center gap-3"><Avatar name={c.name} /><div className="min-w-0"><div className="truncate font-medium">{c.name}</div><div className="truncate text-xs text-muted">{c.email ?? "no email"}{c.country ? ` · ${c.country}` : ""}</div></div></div>
              <div className="flex gap-1"><button aria-label={`Edit ${c.name}`} className="rounded-lg p-1.5 text-muted hover:bg-surface-low" onClick={() => setEdit(c)}><Pencil className="h-4 w-4" /></button>
                <button aria-label={`Delete ${c.name}`} className="rounded-lg p-1.5 text-muted hover:bg-danger-container hover:text-danger-dark" onClick={async () => { if (confirm(`Delete ${c.name}?`)) { try { await del.mutateAsync(c.id); toast("Customer deleted"); } catch (e) { toast((e as Error).message, "error"); } } }}><Trash2 className="h-4 w-4" /></button></div>
            </li>))}</ul>
        )}
      </Card>
      <Modal open={!!edit} onClose={() => setEdit(null)} title={edit?.id ? "Edit customer" : "Add customer"}>
        {edit && <div className="space-y-3">
          <Field label="Name"><Input value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} autoFocus /></Field>
          <Field label="Email"><Input type="email" value={edit.email ?? ""} onChange={(e) => setEdit({ ...edit, email: e.target.value })} /></Field>
          <div className="grid gap-3 sm:grid-cols-2"><Field label="Phone"><Input value={edit.phone ?? ""} onChange={(e) => setEdit({ ...edit, phone: e.target.value })} /></Field><Field label="Country (2-letter)"><Input maxLength={2} value={edit.country ?? ""} onChange={(e) => setEdit({ ...edit, country: e.target.value })} /></Field></div>
          <Field label="Address"><Textarea value={edit.address ?? ""} onChange={(e) => setEdit({ ...edit, address: e.target.value })} /></Field>
          <div className="flex justify-end gap-2"><Button variant="ghost" onClick={() => setEdit(null)}>Cancel</Button><Button onClick={save} disabled={!edit.name?.trim()} loading={create.isPending || update.isPending}>Save</Button></div>
        </div>}
      </Modal>
    </div>
  );
}
