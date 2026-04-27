// -nocheck
export type SettingPath = string;
export type SettingValue = unknown;
export type Settings = SettingsStore;

const defaults: Record<string, unknown> = {
	"tui.maxInlineImageColumns": 80,
	"tui.maxInlineImageRows": 24,
	"tui.inlineImages": true,
	"theme.dark": "dark",
	"theme.light": "light",
	"theme.symbols": "unicode",
	"editor.custom": false,
	"editor.historySearch": true,
	"model.default": "",
	"statusLine.enabled": true,
	"stt.enabled": false,
	"compaction.enabled": true,
};

export class SettingsStore {
	#values = new Map<string, unknown>();

	constructor(overrides?: Record<string, unknown>) {
		for (const [key, value] of Object.entries(defaults)) this.#values.set(key, value);
		for (const [key, value] of Object.entries(overrides ?? {})) this.#values.set(key, value);
	}

	get<T = unknown>(key: string): T {
		return this.#values.get(key) as T;
	}

	set(key: string, value: unknown): void {
		this.#values.set(key, value);
	}

	has(key: string): boolean {
		return this.#values.has(key);
	}

	all(): Record<string, unknown> {
		return Object.fromEntries(this.#values.entries());
	}

	static isolated(overrides?: Record<string, unknown>): SettingsStore {
		return new SettingsStore(overrides);
	}
}

export const settings = new SettingsStore();
