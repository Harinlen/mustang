// -nocheck
export interface ResizedImage {
	data: string | Uint8Array;
	mimeType?: string;
	width?: number;
	height?: number;
}

export async function resizeImage(image: ResizedImage): Promise<ResizedImage> {
	return image;
}

export function formatDimensionNote(result: ResizedImage): string | undefined {
	return result.width && result.height ? `${result.width}x${result.height}` : undefined;
}
