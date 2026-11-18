"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import OutboundOrderForm from "@/components/inventory/OutboundOrderForm";
import { ApiError, listProducts } from "@/lib/inventory";
import type { Asset } from "@/lib/inventory-types";
import ErrorNotice from "@/components/ErrorNotice";
import { CONNECTION_ERROR_MESSAGE } from "@/lib/http-errors";
import { useSectionView } from "@/lib/useSectionView";

function OutboundOrderPageContent() {
  useSectionView("inventory_outbound");
  const searchParams = useSearchParams();
  const assetIdParam = searchParams.get("asset_id");

  const [products, setProducts] = useState<Asset[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;

    listProducts()
      .then((data) => {
        if (!cancelled) setProducts(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof ApiError ? err.message : CONNECTION_ERROR_MESSAGE
          );
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [attempt]);

  function retry() {
    setError(null);
    setIsLoading(true);
    setAttempt((n) => n + 1);
  }

  return (
    <section className="px-6 py-12">
      <div className="mx-auto w-full max-w-6xl">
        <header className="mb-8">
          <p className="font-mono text-xs uppercase tracking-widest text-teal-700">
            Nexova · Inventario
          </p>
          <h1 className="mt-2 text-4xl font-medium tracking-tight text-stone-900">
            Registrar salida
          </h1>
          <p className="mt-3 max-w-2xl text-base leading-7 text-stone-600">
            Registra una asignación a empleado o un consumo. El stock disponible se
            consulta al elegir el activo, antes de introducir la cantidad.
          </p>
        </header>

        {error && <ErrorNotice message={error} onRetry={retry} className="mb-6 max-w-lg" />}

        {isLoading ? (
          <p className="text-sm text-stone-500">Cargando activos...</p>
        ) : (
          <OutboundOrderForm
            products={products}
            initialAssetId={assetIdParam ? Number(assetIdParam) : null}
          />
        )}
      </div>
    </section>
  );
}

export default function OutboundOrderPage() {
  return (
    <Suspense fallback={<p className="px-6 py-12 text-sm text-stone-500">Cargando...</p>}>
      <OutboundOrderPageContent />
    </Suspense>
  );
}
