// -nocheck
export interface OutputTruncationMeta {
	truncated?: boolean;
	hiddenLines?: number;
}

export function formatTruncationMetaNotice(meta?: OutputTruncationMeta): string {
	return meta?.truncated ? `truncated${meta.hiddenLines ? `, ${meta.hiddenLines} hidden lines` : ""}` : "";
}
