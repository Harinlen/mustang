import * as path from "node:path";

export const APP_NAME = "mustang";

export const $env = new Proxy({}, { get: (_target, prop) => process.env[String(prop)] }) as Record<string, string | undefined>;
export function $flag(name: string): boolean { return process.argv.includes(`--${name}`); }
export function $which(_name: string): string | undefined { return undefined; }
export function $envpos(_name: string): number | undefined { return undefined; }

export const logger = {
	debug: (...args: unknown[]) => console.debug(...args),
	info: (...args: unknown[]) => console.info(...args),
	warn: (...args: unknown[]) => console.warn(...args),
	error: (...args: unknown[]) => console.error(...args),
	time: (_label: string) => ({ end: () => {} }),
};

export function getProjectDir(): string { return process.cwd(); }
export function getAgentDir(): string { return path.join(process.cwd(), ".mustang"); }
export function getAgentDbPath(): string { return path.join(getAgentDir(), "agent.db"); }
export function getDebugLogPath(): string { return path.join(getAgentDir(), "debug.log"); }
export function getCustomThemesDir(): string { return path.join(getAgentDir(), "themes"); }

let defaultTabWidth = 4;
export function getDefaultTabWidth(): number { return defaultTabWidth; }
export function setDefaultTabWidth(width: number): void { defaultTabWidth = width; }
export function getIndentation(_file?: string, _cwd?: string): number { return defaultTabWidth; }

export function formatNumber(value: number): string { return Number(value ?? 0).toLocaleString(); }
export function formatCount(value: number): string { return formatNumber(value); }
export function formatDuration(ms: number): string { return `${Math.round(ms / 1000)}s`; }
export function formatAge(_date: Date | number | string): string { return ""; }
export function formatBytes(bytes: number): string { return `${bytes} B`; }
export function pluralize(word: string, count: number): string { return count === 1 ? word : `${word}s`; }

export function isEnoent(error: unknown): boolean { return (error as { code?: string })?.code === "ENOENT"; }
export const postmortem = { add: () => {}, remove: () => {} };
export async function prompt(_message: string): Promise<string> { return ""; }
prompt.render = (message: string) => message;

export function adjustHsv(hex: string): string { return hex; }
export function hsvToRgb(): [number, number, number] { return [255, 255, 255]; }

export const procmgr = {
	add: () => {},
	remove: () => {},
	getShellConfig: () => ({}),
};

export class Snowflake {
	static next(): string { return `${Date.now()}`; }
	next(): string { return Snowflake.next(); }
}

export function parseFrontmatter(content: string): { attributes: Record<string, unknown>; body: string } {
	return { attributes: {}, body: content };
}

export async function abortableSleep(ms: number): Promise<void> {
	await new Promise(resolve => setTimeout(resolve, ms));
}

export function setNativeKillTree(_fn: unknown): void {}

export const SUPPORTED_IMAGE_MIME_TYPES = ["image/png", "image/jpeg", "image/webp"];
export async function readImageMetadata(): Promise<{ width: number; height: number; mimeType: string } | null> { return null; }

export async function renderMermaidAsciiSafe(_source: string): Promise<string | null> {
	return null;
}
