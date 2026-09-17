import { useMemo } from "react";
import { EXIT_TYPE_LABELS } from "@/lib/inventory-labels";
import type { Order } from "@/lib/inventory-types";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("es-ES", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export default function OrdersHistoryTable({ orders }: { orders: Order[] }) {
  const sorted = useMemo(
    () =>
      [...orders].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      ),
    [orders]
  );

  if (orders.length === 0) {
    return (
      <p className="rounded-lg border border-stone-200 bg-white px-4 py-6 text-center text-sm text-stone-500">
        No hay órdenes registradas todavía.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-stone-200 bg-white">
      <table className="w-full min-w-[860px] text-left text-sm">
        <thead>
          <tr className="border-b border-stone-200 text-xs uppercase tracking-wide text-stone-500">
            <th className="px-4 py-3 font-medium">Tipo</th>
            <th className="px-4 py-3 font-medium">Producto</th>
            <th className="px-4 py-3 font-medium">Cantidad</th>
            <th className="px-4 py-3 font-medium">Detalle</th>
            <th className="px-4 py-3 font-medium">Oficina</th>
            <th className="px-4 py-3 font-medium">Fecha</th>
            <th className="px-4 py-3 font-medium">Usuario</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((order) => {
            const isInbound = order.order_type === "inbound";
            return (
              <tr key={`${order.order_type}-${order.id}`} className="border-b border-stone-100 last:border-0">
                <td className="px-4 py-3">
                  <span
                    className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${
                      isInbound
                        ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                        : "border-sky-200 bg-sky-50 text-sky-700"
                    }`}
                  >
                    <span className={`h-1.5 w-1.5 rounded-full ${isInbound ? "bg-emerald-500" : "bg-sky-500"}`} />
                    {isInbound ? "Entrada" : "Salida"}
                  </span>
                </td>
                <td className="px-4 py-3 text-stone-900">
                  {order.asset.name}
                  <span className="ml-1.5 font-mono text-xs text-stone-500">{order.asset.sku}</span>
                </td>
                <td className="px-4 py-3 text-stone-700">{order.quantity}</td>
                <td className="px-4 py-3 text-stone-600">
                  {isInbound
                    ? order.supplier
                    : `${order.exit_type ? EXIT_TYPE_LABELS[order.exit_type] : ""}${
                        order.assigned_to ? ` — ${order.assigned_to}` : ""
                      }`}
                </td>
                <td className="px-4 py-3 text-stone-600">{order.office}</td>
                <td className="px-4 py-3 text-stone-600">{formatDate(order.created_at)}</td>
                <td className="px-4 py-3 font-mono text-xs text-stone-500">{order.user_uuid}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
