"use client";

import { useCallback, useRef, useState } from "react";

interface PdfUploaderProps {
  onFileSelected: (file: File) => void;
  onInvalidFile: (message: string) => void;
  isUploading: boolean;
}

/** Zona de subida de RFPs (solo PDF). Mismo diseño que el cargador de incidencias. */
export default function PdfUploader({ onFileSelected, onInvalidFile, isUploading }: PdfUploaderProps) {
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFiles = useCallback(
    (files: FileList | null) => {
      if (!files || files.length === 0 || isUploading) return;
      const file = files[0];
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        onInvalidFile("Las RFP se suben siempre en PDF.");
        return;
      }
      onFileSelected(file);
      if (inputRef.current) inputRef.current.value = "";
    },
    [isUploading, onFileSelected, onInvalidFile]
  );

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setIsDragging(false);
        handleFiles(e.dataTransfer.files);
      }}
      onClick={() => inputRef.current?.click()}
      role="button"
      tabIndex={0}
      aria-busy={isUploading}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
      }}
      className={`group cursor-pointer rounded-xl border-2 border-dashed px-8 py-12 text-center transition-colors ${
        isDragging
          ? "border-teal-500 bg-teal-400/10"
          : "border-ink-700/20 bg-white hover:border-teal-500/60"
      }`}
    >
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        className="hidden"
        onChange={(e) => handleFiles(e.target.files)}
      />

      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-ink-900 text-paper-100">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M12 16V4M12 4l-4 4M12 4l4 4" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M4 16v3a2 2 0 002 2h12a2 2 0 002-2v-3" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>

      {isUploading ? (
        <p className="font-display text-base font-medium text-ink-900">Subiendo RFP…</p>
      ) : (
        <>
          <p className="font-display text-base font-medium text-ink-900">Arrastra aquí la RFP en PDF</p>
          <p className="mt-1 text-sm text-ink-900/60">
            o haz clic para seleccionarla. Se crea un ticket y el análisis corre en segundo plano.
          </p>
        </>
      )}
    </div>
  );
}
