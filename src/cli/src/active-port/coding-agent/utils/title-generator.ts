// -nocheck
export async function generateSessionTitle(text: string): Promise<string> {
	return text.trim().split(/\s+/).slice(0, 6).join(" ") || "New session";
}

export function setTerminalTitle(_title: string): void {}
export function setSessionTerminalTitle(_title: string): void {}
export function pushTerminalTitle(): void {}
export function popTerminalTitle(): void {}
export function formatSessionTerminalTitle(title: string): string {
	return title;
}
