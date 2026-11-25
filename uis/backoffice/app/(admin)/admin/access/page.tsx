"use client";

import { useCallback, useState, type FormEvent } from "react";
import { FIELD_INPUT, FIELD_LABEL, SUBMIT_BUTTON } from "@/components/account/form-styles";
import { CARD, EmptyState, Resource } from "@/components/dashboard/Resource";
import { useAuth } from "@/lib/AuthContext";
import { can } from "@/lib/permissions";
import {
  RbacError,
  assignUser,
  createDepartment,
  createRole,
  listDepartments,
  listRoles,
  listUsers,
  updateDepartment,
  updateRole,
  type AdminUser,
  type Department,
  type Role,
} from "@/lib/rbac";
import { useAsyncResource } from "@/lib/useAsyncResource";

const BUTTON_SECONDARY =
  "rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-sm text-stone-700 transition hover:border-teal-600 hover:text-teal-700 disabled:cursor-not-allowed disabled:opacity-50";
const ERROR_LINE = "rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700";
const NO_DEPARTMENT = "";

const loadRoles = listRoles;
const loadDepartments = listDepartments;
const loadUsers = listUsers;

function describe(err: unknown): string {
  return err instanceof RbacError ? err.message : "No se pudo completar la operación. Inténtalo de nuevo.";
}

export default function AccessAdminPage() {
  const { user } = useAuth();

  if (!can(user, "administer")) {
    return (
      <section className="px-6 py-12">
        <div className="mx-auto w-full max-w-3xl">
          <p role="alert" className={ERROR_LINE}>
            Esta vista es solo para administradores. Tu rol no permite administrar roles, departamentos ni usuarios.
          </p>
        </div>
      </section>
    );
  }

  return <AccessAdmin />;
}

function AccessAdmin() {
  const roles = useAsyncResource(loadRoles);
  const departments = useAsyncResource(loadDepartments);
  const users = useAsyncResource(loadUsers);

  return (
    <section className="px-6 py-12">
      <div className="mx-auto w-full max-w-6xl space-y-10">
        <header>
          <p className="text-xs font-semibold uppercase tracking-widest text-teal-700">Administración</p>
          <h1 className="mt-2 text-3xl font-medium tracking-tight text-stone-900">Roles y departamentos</h1>
          <p className="mt-3 max-w-2xl text-base leading-7 text-stone-600">
            El <strong>rol</strong> define qué puede hacer una persona; el <strong>departamento</strong> define qué datos le
            competen. Se asignan por separado: cambiar uno no modifica el otro.
          </p>
        </header>

        <div className="grid gap-6 lg:grid-cols-2">
          <section className={CARD} aria-labelledby="roles-title">
            <h2 id="roles-title" className="text-lg font-medium text-stone-900">Roles</h2>
            <Resource state={roles.state} onRetry={roles.reload}>
              {(data) => <RolesPanel roles={data} onChanged={roles.reload} />}
            </Resource>
          </section>
          <section className={CARD} aria-labelledby="departments-title">
            <h2 id="departments-title" className="text-lg font-medium text-stone-900">Departamentos</h2>
            <Resource state={departments.state} onRetry={departments.reload}>
              {(data) => <DepartmentsPanel departments={data} onChanged={departments.reload} />}
            </Resource>
          </section>
        </div>

        <section className={CARD} aria-labelledby="users-title">
          <h2 id="users-title" className="text-lg font-medium text-stone-900">Asignación de usuarios</h2>
          <Resource state={users.state} onRetry={users.reload}>
            {(data) => (
              <Resource state={roles.state} onRetry={roles.reload}>
                {(roleList) => (
                  <Resource state={departments.state} onRetry={departments.reload}>
                    {(departmentList) => (
                      <UsersPanel users={data} roles={roleList} departments={departmentList} onChanged={users.reload} />
                    )}
                  </Resource>
                )}
              </Resource>
            )}
          </Resource>
        </section>
      </div>
    </section>
  );
}

function RolesPanel({ roles, onChanged }: { roles: Role[]; onChanged: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [rank, setRank] = useState("");
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
      return true;
    } catch (err) {
      setError(describe(err));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const onCreate = async (event: FormEvent) => {
    event.preventDefault();
    const parsedRank = Number(rank);
    if (!Number.isInteger(parsedRank)) {
      setError("El rango debe ser un número entero.");
      return;
    }
    if (await run(() => createRole({ code: code.trim(), name: name.trim(), rank: parsedRank }))) {
      setCode("");
      setName("");
      setRank("");
    }
  };

  return (
    <div className="mt-4 space-y-4">
      <ul className="space-y-2">
        {roles.map((role) => (
          <li key={role.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-stone-200 bg-stone-50 px-3 py-2">
            {editing?.id === role.id ? (
              <>
                <input
                  aria-label={`Nombre del rol ${role.code}`}
                  value={editing.name}
                  onChange={(e) => setEditing({ id: role.id, name: e.target.value })}
                  className={FIELD_INPUT + " max-w-xs"}
                />
                <span className="flex gap-2">
                  <button type="button" disabled={busy || !editing.name.trim()} className={BUTTON_SECONDARY}
                    onClick={async () => { if (await run(() => updateRole(role.id, { name: editing.name.trim() }))) setEditing(null); }}>
                    Guardar
                  </button>
                  <button type="button" className={BUTTON_SECONDARY} onClick={() => setEditing(null)}>Cancelar</button>
                </span>
              </>
            ) : (
              <>
                <span>
                  <span className="font-medium text-stone-900">{role.name}</span>{" "}
                  <span className="font-mono text-xs text-stone-500">{role.code} · rango {role.rank}</span>
                </span>
                <button type="button" className={BUTTON_SECONDARY} onClick={() => setEditing({ id: role.id, name: role.name })}>Editar</button>
              </>
            )}
          </li>
        ))}
      </ul>

      <form onSubmit={onCreate} className="grid gap-3 sm:grid-cols-3" aria-label="Crear rol">
        <label className={FIELD_LABEL}>Código
          <input required value={code} onChange={(e) => setCode(e.target.value)} placeholder="auditor" className={FIELD_INPUT} />
        </label>
        <label className={FIELD_LABEL}>Nombre
          <input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Auditor" className={FIELD_INPUT} />
        </label>
        <label className={FIELD_LABEL}>Rango
          <input required inputMode="numeric" value={rank} onChange={(e) => setRank(e.target.value)} placeholder="4" className={FIELD_INPUT} />
        </label>
        <div className="sm:col-span-3">
          <button type="submit" disabled={busy} className={SUBMIT_BUTTON}>{busy ? "Guardando…" : "Crear rol"}</button>
        </div>
      </form>
      {error && <p role="alert" className={ERROR_LINE}>{error}</p>}
    </div>
  );
}

function DepartmentsPanel({ departments, onChanged }: { departments: Department[]; onChanged: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
      return true;
    } catch (err) {
      setError(describe(err));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const onCreate = async (event: FormEvent) => {
    event.preventDefault();
    if (await run(() => createDepartment({ code: code.trim(), name: name.trim() }))) {
      setCode("");
      setName("");
    }
  };

  return (
    <div className="mt-4 space-y-4">
      <ul className="space-y-2">
        {departments.map((department) => (
          <li key={department.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-stone-200 bg-stone-50 px-3 py-2">
            {editing?.id === department.id ? (
              <>
                <input
                  aria-label={`Nombre del departamento ${department.code}`}
                  value={editing.name}
                  onChange={(e) => setEditing({ id: department.id, name: e.target.value })}
                  className={FIELD_INPUT + " max-w-xs"}
                />
                <span className="flex gap-2">
                  <button type="button" disabled={busy || !editing.name.trim()} className={BUTTON_SECONDARY}
                    onClick={async () => { if (await run(() => updateDepartment(department.id, { name: editing.name.trim() }))) setEditing(null); }}>
                    Guardar
                  </button>
                  <button type="button" className={BUTTON_SECONDARY} onClick={() => setEditing(null)}>Cancelar</button>
                </span>
              </>
            ) : (
              <>
                <span>
                  <span className="font-medium text-stone-900">{department.name}</span>{" "}
                  <span className="font-mono text-xs text-stone-500">{department.code}</span>
                </span>
                <button type="button" className={BUTTON_SECONDARY} onClick={() => setEditing({ id: department.id, name: department.name })}>Editar</button>
              </>
            )}
          </li>
        ))}
      </ul>

      <form onSubmit={onCreate} className="grid gap-3 sm:grid-cols-2" aria-label="Crear departamento">
        <label className={FIELD_LABEL}>Código
          <input required value={code} onChange={(e) => setCode(e.target.value)} placeholder="legal" className={FIELD_INPUT} />
        </label>
        <label className={FIELD_LABEL}>Nombre
          <input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Legal" className={FIELD_INPUT} />
        </label>
        <div className="sm:col-span-2">
          <button type="submit" disabled={busy} className={SUBMIT_BUTTON}>{busy ? "Guardando…" : "Crear departamento"}</button>
        </div>
      </form>
      {error && <p role="alert" className={ERROR_LINE}>{error}</p>}
    </div>
  );
}

interface UsersPanelProps {
  users: AdminUser[];
  roles: Role[];
  departments: Department[];
  onChanged: () => void;
}

function UsersPanel({ users, roles, departments, onChanged }: UsersPanelProps) {
  if (users.length === 0) return <EmptyState>No hay usuarios.</EmptyState>;
  return (
    <div className="mt-4 overflow-x-auto">
      <table className="min-w-full divide-y divide-stone-200 text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-stone-500">
          <tr>
            <th scope="col" className="px-3 py-2">Usuario</th>
            <th scope="col" className="px-3 py-2">Rol (qué puede hacer)</th>
            <th scope="col" className="px-3 py-2">Departamento (qué datos ve)</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-stone-100">
          {users.map((row) => (
            <UserRow key={`${row.id}:${row.role_id}:${row.department_id ?? ""}`} row={row} roles={roles} departments={departments} onChanged={onChanged} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function UserRow({ row, roles, departments, onChanged }: { row: AdminUser; roles: Role[]; departments: Department[]; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const roleKnown = roles.some((r) => r.id === row.role_id);

  // Cada selector envía SOLO su eje: lo que no se toca no viaja en la petición.
  const change = useCallback(
    async (changes: { role_id?: string; department_id?: string | null }) => {
      setBusy(true);
      setError(null);
      try {
        await assignUser(row.id, changes);
        onChanged();
      } catch (err) {
        setError(describe(err));
      } finally {
        setBusy(false);
      }
    },
    [row.id, onChanged],
  );

  return (
    <tr className="align-top">
      <td className="px-3 py-2">
        <p className="break-all font-medium text-stone-900">{row.email}</p>
        {error && <p role="alert" className="mt-1 text-xs text-red-700">{error}</p>}
      </td>
      <td className="px-3 py-2">
        <select
          aria-label={`Rol de ${row.email}`}
          value={row.role_id}
          disabled={busy}
          onChange={(e) => change({ role_id: e.target.value })}
          className={FIELD_INPUT + " mt-0"}
        >
          {!roleKnown && <option value={row.role_id}>{row.role_id} (cuenta externa)</option>}
          {roles.map((role) => (
            <option key={role.id} value={role.id}>{role.name}</option>
          ))}
        </select>
      </td>
      <td className="px-3 py-2">
        <select
          aria-label={`Departamento de ${row.email}`}
          value={row.department_id ?? NO_DEPARTMENT}
          disabled={busy}
          onChange={(e) => change({ department_id: e.target.value === NO_DEPARTMENT ? null : e.target.value })}
          className={FIELD_INPUT + " mt-0"}
        >
          <option value={NO_DEPARTMENT}>Sin departamento</option>
          {departments.map((department) => (
            <option key={department.id} value={department.id}>{department.name}</option>
          ))}
        </select>
      </td>
    </tr>
  );
}
