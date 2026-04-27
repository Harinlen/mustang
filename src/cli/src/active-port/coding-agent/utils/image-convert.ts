// -nocheck
export async function convertToPng(data: string | Uint8Array): Promise<Uint8Array> {
	return typeof data === "string" ? new TextEncoder().encode(data) : data;
}
