"use client";

import { useEffect, useState } from "react";
import { ApiError, createOutboundOrder, getProduct } from "@/lib/inventory";
import { OFFICES, type Asset, type ExitType, type Office } from "@/lib/inventory-types";

export default function OutboundOrderForm({
  products,
  initialAssetId,
}: {
  products: Asset[];
  initialAssetId: number | null;
}) {
  const [assetId, setAssetId] = useState<string>(
    initialAssetId ? String(initialAssetId) : products[0] ? String(products[0].id) : ""
  );
  const [availableStock, setAvailableStock] = useState<number | null>(null);
  const [isCheckingStock, setIsCheckingStock] = useState(false);

  const [quantity, setQuantity] = useState("");
  const [exitType, setExitType] = useState<ExitType>("consumption");
  const [assignedTo, setAssignedTo] = useState("");
  const [office, setOffice] = useState<Office>(OFFICES[0]);

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // Stock reactivo: se re-consulta el activo cada vez que cambia la selección,
  // así el valor mostrado nunca queda obsoleto respecto a órdenes recientes.
  useEffect(() => {
    if (!assetId) {
      setAvailableStock(null);
      return;
    }

    let cancelled = false;
    setIsCheckingStock(true);
    setAvailableStock(null);

    getProduct(Number(assetId))
      .then((asset) => {
        if (!cancelled) setAvailableStock(asset.current_stock);
      })
      .catch(() => {
        if (!cancelled) setAvailableStock(null);
      })
      .finally(() => {
        if (!cancelled) setIsCheckingStock(false);
      });

    return () => {
      cancelled = true;
    };
  }, [assetId]);

  const parsedQuantity = Number(quantity);
  const exceedsStock =
    availableStock !== null && parsedQuantity > 0 && parsedQuantity > availableStock;

  const resetForm = () => {
    setQuantity("");
    setAssignedTo("");
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setFieldError(null);
    setSuccess(false);

    if (!assetId) {
      setError("Selecciona un activo.");
      return;
    }
    if (!parsedQuantity || parsedQuantity <= 0) {
      setError("La cantidad debe ser mayor que cero.");
      return;
    }
    if (exceedsStock) {
      // Salvaguarda de UX — la regla real la aplica la API (HTTP 400).
      setFieldError(
        `La cantidad supera el stock disponible (${availableStock}). Corrige antes de enviar.`
      );
      return;
    }
    if (exitType === "allocation" && !assignedTo.trim()) {
      setError("'Asignado a' es obligatorio cuando el tipo de salida es 'Asignación'.");
      return;
    }

    setIsSubmitting(true);
    try {
      const exit = await createOutboundOrder({
        asset_id: Number(assetId),
        quantity: parsedQuantity,
        exit_type: exitType,
        assigned_to: exitType === "allocation" ? assignedTo.trim() : null,
        office,
      });
      setSuccess(true);
      resetForm();
      // La propia respuesta ya trae el stock resultante: no hace falta
      // otra llamada a getProduct() para refrescarlo.
      setAvailableStock(exit.current_stock);
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        setFieldError(err.message);
      } else {
        setError(
          err instanceof ApiError
            ? err.message
            : "No se pudo conectar con la API. Comprueba que el backend esté en marcha."
        );
      }
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
          onChange={(event) => {
            setAssetId(event.target.value);
            setFieldError(null);
          }}
          className="mt-1 w-full rounded-md border border-stone-300 px-3 py-2 text-sm text-stone-900 focus:border-teal-600 focus:outline-none"
        >
          {products.length === 0 && <option value="">No hay activos disponibles</option>}
          {products.map((asset) => (
            <option key={asset.id} value={asset.id}>
              {asset.name} — {asset.sku} ({asset.office})
            </option>
          ))}
        </select>
        <p className="mt-2 text-sm text-stone-600">
          Stock disponible:{" "}
          <span className="font-medium text-stone-900">
            {isCheckingStock ? "consultando..." : availableStock ?? "—"}
          </span>
        </p>
      </div>

      <div>
        <label htmlFor="quantity" className="block text-sm font-medium text-stone-700">
          Cantidad a retirar
        </label>
        <input
          id="quantity"
          type="number"
          min={1}
          value={quantity}
          onChange={(event) => {
            setQuantity(event.target.value);
            setFieldError(null);
          }}
          className={`mt-1 w-full rounded-md border px-3 py-2 text-sm text-stone-900 focus:outline-none ${
            exceedsStock
              ? "border-red-400 focus:border-red-500"
              : "border-stone-300 focus:border-teal-600"
          }`}
        />
        {exceedsStock && (
          <p className="mt-1 text-sm text-red-600">
            La cantidad ({parsedQuantity}) supera el stock disponible ({availableStock}).
          </p>
        )}
        {fieldError && <p className="mt-1 text-sm text-red-600">{fieldError}</p>}
      </div>

      <div>
        <label htmlFor="exit_type" className="block text-sm font-medium text-stone-700">
          Tipo de salida
        </label>
        <select
          id="exit_type"
          value={exitType}
          onChange={(event) => setExitType(event.target.value as ExitType)}
          className="mt-1 w-full rounded-md border border-stone-300 px-3 py-2 text-sm text-stone-900 focus:border-teal-600 focus:outline-none"
        >
          <option value="consumption">Consumo</option>
          <option value="allocation">Asignación a empleado</option>
        </select>
      </div>

      {exitType === "allocation" && (
        <div>
          <label htmlFor="assigned_to" className="block text-sm font-medium text-stone-700">
            Asignado a
          </label>
          <input
            id="assigned_to"
            type="text"
            value={assignedTo}
            onChange={(event) => setAssignedTo(event.target.value)}
            placeholder="Nombre del empleado"
            className="mt-1 w-full rounded-md border border-stone-300 px-3 py-2 text-sm text-stone-900 focus:border-teal-600 focus:outline-none"
          />
        </div>
      )}

      <div>
        <label htmlFor="office" className="block text-sm font-medium text-stone-700">
          Oficina
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
          Salida registrada correctamente.
        </div>
      )}

      <button
        type="submit"
        disabled={isSubmitting || products.length === 0 || exceedsStock}
        className="w-full rounded-full bg-teal-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {isSubmitting ? "Registrando..." : "Registrar salida"}
      </button>
    </form>
  );
}
