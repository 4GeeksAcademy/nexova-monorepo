"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, createInboundOrder } from "@/lib/inventory";
import { OFFICES, type Asset, type Office } from "@/lib/inventory-types";
import { flushOnHide, track } from "@/lib/telemetry";

export default function InboundOrderForm({
  products,
  initialAssetId,
}: {
  products: Asset[];
  initialAssetId: number | null;
}) {
  const [assetId, setAssetId] = useState<string>(
    initialAssetId ? String(initialAssetId) : products[0] ? String(products[0].id) : ""
  );
  const [quantity, setQuantity] = useState("");
  const [supplier, setSupplier] = useState("");
  const [office, setOffice] = useState<Office>(OFFICES[0]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // inventory_order_form_abandoned: se dispara una sola vez por intento de
  // formulario, al desmontar o cerrar/recargar la página, si no hubo un
  // submit exitoso previo.
  const formStateRef = useRef({ assetId, quantity, supplier, office });
  useEffect(() => {
    formStateRef.current = { assetId, quantity, supplier, office };
  }, [assetId, quantity, supplier, office]);

  const submittedRef = useRef(false);
  const abandonReportedRef = useRef(false);
  const mountedAtRef = useRef<number | null>(null);

  useEffect(() => {
    mountedAtRef.current = Date.now();

    const reportAbandonIfNeeded = () => {
      if (submittedRef.current || abandonReportedRef.current) {
        return;
      }
      abandonReportedRef.current = true;
      const values = formStateRef.current;
      const fieldsFilledCount = [values.assetId, values.quantity, values.supplier, values.office].filter(
        Boolean
      ).length;
      track("inventory_order_form_abandoned", {
        form: "inbound",
        fields_filled_count: fieldsFilledCount,
        time_on_form_ms: Date.now() - (mountedAtRef.current ?? Date.now()),
      });
      // Un cierre/navegación inmediata puede destruir la página antes de que
      // el navegador dispare `visibilitychange` — forzamos el envío ya
      // (mismo fix aplicado a session_expired en lib/session.ts).
      flushOnHide();
    };

    window.addEventListener("beforeunload", reportAbandonIfNeeded);
    return () => {
      window.removeEventListener("beforeunload", reportAbandonIfNeeded);
      reportAbandonIfNeeded();
    };
  }, []);

  const resetForm = () => {
    setQuantity("");
    setSupplier("");
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setSuccess(false);

    const parsedQuantity = Number(quantity);
    if (!assetId) {
      setError("Selecciona un activo.");
      return;
    }
    if (!parsedQuantity || parsedQuantity <= 0) {
      setError("La cantidad debe ser mayor que cero.");
      return;
    }
    if (!supplier.trim()) {
      setError("El proveedor es obligatorio.");
      return;
    }

    setIsSubmitting(true);
    try {
      await createInboundOrder({
        asset_id: Number(assetId),
        quantity: parsedQuantity,
        supplier: supplier.trim(),
        office,
      });
      submittedRef.current = true;
      setSuccess(true);
      resetForm();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "No se pudo conectar con la API. Comprueba que el backend esté en marcha."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="max-w-lg space-y-5 rounded-lg border border-stone-200 bg-white p-6">
      <div>
        <label htmlFor="asset" className="block text-sm font-medium text-stone-700">
          Activo
        </label>
        <select
          id="asset"
          value={assetId}
          onChange={(event) => setAssetId(event.target.value)}
          className="mt-1 w-full rounded-md border border-stone-300 px-3 py-2 text-sm text-stone-900 focus:border-teal-600 focus:outline-none"
        >
          {products.length === 0 && <option value="">No hay activos disponibles</option>}
          {products.map((asset) => (
            <option key={asset.id} value={asset.id}>
              {asset.name} — {asset.sku} ({asset.office})
            </option>
          ))}
        </select>
      </div>

      <div>
        <label htmlFor="quantity" className="block text-sm font-medium text-stone-700">
          Cantidad recibida
        </label>
        <input
          id="quantity"
          type="number"
          min={1}
          value={quantity}
          onChange={(event) => setQuantity(event.target.value)}
          className="mt-1 w-full rounded-md border border-stone-300 px-3 py-2 text-sm text-stone-900 focus:border-teal-600 focus:outline-none"
        />
      </div>

      <div>
        <label htmlFor="supplier" className="block text-sm font-medium text-stone-700">
          Proveedor
        </label>
        <input
          id="supplier"
          type="text"
          value={supplier}
          onChange={(event) => setSupplier(event.target.value)}
          placeholder='Ej.: "TechDistrib Valencia S.L."'
          className="mt-1 w-full rounded-md border border-stone-300 px-3 py-2 text-sm text-stone-900 focus:border-teal-600 focus:outline-none"
        />
      </div>

      <div>
        <label htmlFor="office" className="block text-sm font-medium text-stone-700">
          Oficina receptora
        </label>
        <select
          id="office"
          value={office}
          onChange={(event) => setOffice(event.target.value as Office)}
          className="mt-1 w-full rounded-md border border-stone-300 px-3 py-2 text-sm text-stone-900 focus:border-teal-600 focus:outline-none"
        >
          {OFFICES.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {success && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          Entrada registrada correctamente.
        </div>
      )}

      <button
        type="submit"
        disabled={isSubmitting || products.length === 0}
        className="w-full rounded-full bg-teal-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {isSubmitting ? "Registrando..." : "Registrar entrada"}
      </button>
    </form>
  );
}
