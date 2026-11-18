import Link from "next/link";
import { CATEGORY_LABELS } from "@/lib/inventory-labels";
import type { Asset } from "@/lib/inventory-types";
import StockBadge from "./StockBadge";

export default function ProductsTable({ products }: { products: Asset[] }) {
  if (products.length === 0) {
    return (
      <p className="rounded-lg border border-stone-200 bg-white px-4 py-6 text-center text-sm text-stone-500">
        No hay activos registrados todavía.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-stone-200 bg-white">
      <table className="w-full min-w-[720px] text-left text-sm">
        <thead>
          <tr className="border-b border-stone-200 text-xs uppercase tracking-wide text-stone-500">
            <th className="px-4 py-3 font-medium">Nombre</th>
            <th className="px-4 py-3 font-medium">SKU</th>
            <th className="px-4 py-3 font-medium">Categoría</th>
            <th className="px-4 py-3 font-medium">Oficina</th>
            <th className="px-4 py-3 font-medium">Stock actual</th>
            <th className="px-4 py-3 font-medium">Acciones</th>
          </tr>
        </thead>
        <tbody>
          {products.map((asset) => (
            <tr key={asset.id} className="border-b border-stone-100 last:border-0">
              <td className="px-4 py-3 font-medium text-stone-900">{asset.name}</td>
              <td className="px-4 py-3 font-mono text-xs text-stone-600">{asset.sku}</td>
              <td className="px-4 py-3 text-stone-600">{CATEGORY_LABELS[asset.category]}</td>
              <td className="px-4 py-3 text-stone-600">{asset.office}</td>
              <td className="px-4 py-3">
                <StockBadge currentStock={asset.current_stock} />
              </td>
              <td className="px-4 py-3">
                <div className="flex gap-2">
                  <Link
                    href={`/inventory/orders/inbound?asset_id=${asset.id}`}
                    className="rounded-lg border border-stone-300 bg-white px-3 py-1 text-xs font-medium text-stone-700 transition hover:border-teal-600 hover:text-teal-700"
                  >
                    Registrar entrada
                  </Link>
                  <Link
                    href={`/inventory/orders/outbound?asset_id=${asset.id}`}
                    className="rounded-lg border border-stone-300 bg-white px-3 py-1 text-xs font-medium text-stone-700 transition hover:border-teal-600 hover:text-teal-700"
                  >
                    Registrar salida
                  </Link>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
