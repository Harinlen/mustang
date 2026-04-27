// -nocheck
export interface ExitPlanModeDetails {
	plan?: string;
	approved?: boolean;
}

export interface LspStartupServerInfo {
	name: string;
	status?: string;
}

export interface EditToolDetails {
	filePath?: string;
	operation?: string;
}

export type ToolDetails = Record<string, unknown>;
