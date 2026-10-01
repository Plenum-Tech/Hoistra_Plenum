"use client";
import { useState, useRef } from "react";
import { Upload, FileSpreadsheet } from "lucide-react";
import { useMigrationStartUpload } from "../../chat-api";

const CMMS_OPTIONS = [
  "Fiix", "Maximo", "SAP PM", "ServiceNow", "eMaint", "Hippo CMMS",
  "Asset Panda", "Limble CMMS", "UpKeep", "Prometheus", "Custom",
];

function detectCmms(fileName: string): string {
  const lower = fileName.toLowerCase();
  for (const name of CMMS_OPTIONS) {
    if (lower.includes(name.toLowerCase())) return name;
  }
  return "";
}

interface Props {
  orgId: string;
  onStarted: (migrationId: string) => void;
}

export default function UploadPanel({ orgId, onStarted }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [cmmsName, setCmmsName] = useState("");
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const { mutate: startUpload, isPending, error } = useMigrationStartUpload({
    onSuccess: (res) => onStarted(res.migration_id),
  });

  function handleFile(f: File) {
    setFile(f);
    const detected = detectCmms(f.name);
    if (detected) setCmmsName(detected);
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) handleFile(f);
  }

  function handleSubmit() {
    if (!file || !cmmsName) return;
    startUpload({ file, cmms_name: cmmsName, organization_id: orgId });
  }

  const isValid = !!file && !!cmmsName.trim();

  return (
    <div className="max-w-2xl">
      <div className="mb-6">
        <h2 className="text-xl font-bold text-slate-900">Data Migration</h2>
        <p className="text-sm text-slate-500 mt-1">
          Upload a CSV or Excel file from your existing CMMS to map and migrate it
          into Plenum CAFM using a 9-node AI pipeline with HITL review gates.
        </p>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6 space-y-5">
        {/* Drop zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          onClick={() => fileRef.current?.click()}
          className={`flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed p-10 cursor-pointer transition-colors ${
            dragging ? "border-indigo-400 bg-indigo-50" : "border-slate-200 hover:border-slate-300 hover:bg-slate-50"
          }`}
        >
          <input
            ref={fileRef}
            type="file"
            className="hidden"
            accept=".csv,.xlsx,.xls"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
          />
          {file ? (
            <>
              <FileSpreadsheet size={32} className="text-green-500" />
              <div className="text-center">
                <p className="text-sm font-semibold text-slate-800">{file.name}</p>
                <p className="text-xs text-slate-400 mt-0.5">{(file.size / 1024).toFixed(1)} KB</p>
              </div>
              <p className="text-xs text-slate-400">Click or drop to replace</p>
            </>
          ) : (
            <>
              <Upload size={32} className="text-slate-400" />
              <div className="text-center">
                <p className="text-sm font-semibold text-slate-700">Drop your file here</p>
                <p className="text-xs text-slate-400 mt-0.5">CSV, XLSX, or XLS — click to browse</p>
              </div>
            </>
          )}
        </div>

        {/* CMMS selector */}
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">CMMS system</label>
          <select
            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            value={cmmsName}
            onChange={(e) => setCmmsName(e.target.value)}
          >
            <option value="">Select your CMMS…</option>
            {CMMS_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        </div>

        {error != null && (
          <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
            {error instanceof Error ? error.message : "Failed to start migration"}
          </div>
        )}

        <button
          className="inline-flex w-full items-center justify-center gap-2 px-4 py-3 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
          onClick={handleSubmit}
          disabled={isPending || !isValid}
        >
          {isPending ? (
            <>
              <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              Starting migration…
            </>
          ) : (
            <>
              <Upload size={16} />
              Start migration
            </>
          )}
        </button>
      </div>
    </div>
  );
}
