export const ASSET_CATEGORIES = [
  "hardware",
  "peripherals",
  "office_supplies",
  "training_materials",
] as const;

export type AssetCategory = (typeof ASSET_CATEGORIES)[number];

export const OFFICES = ["Valencia", "Miami"] as const;

export type Office = (typeof OFFICES)[number];

export type ExitType = "allocation" | "consumption";

export interface Asset {
  id: number;
  name: string;
  sku: string;
  category: AssetCategory;
  office: Office;
  current_stock: number;
}

export interface AssetCreatePayload {
  name: string;
  sku: string;
  category: AssetCategory;
  office: Office;
}

export interface AssetEntryCreatePayload {
  asset_id: number;
  quantity: number;
  supplier: string;
  office: Office;
}

export interface AssetEntry {
  id: number;
  asset_id: number;
  quantity: number;
  supplier: string;
  office: Office;
  created_at: string;
  user_uuid: string;
}

export interface AssetExitCreatePayload {
  asset_id: number;
  quantity: number;
  exit_type: ExitType;
  assigned_to?: string | null;
  office: Office;
}

export interface AssetExit {
  id: number;
  asset_id: number;
  quantity: number;
  exit_type: ExitType;
  assigned_to: string | null;
  office: Office;
  created_at: string;
  user_uuid: string;
  current_stock: number;
}

export interface AssetSummary {
  id: number;
  name: string;
  sku: string;
}

export interface Order {
  order_type: "inbound" | "outbound";
  id: number;
  asset: AssetSummary;
  quantity: number;
  office: Office;
  created_at: string;
  user_uuid: string;
  supplier?: string | null;
  exit_type?: ExitType | null;
  assigned_to?: string | null;
}
