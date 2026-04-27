// -nocheck
export interface HistoryEntry {
	text: string;
	timestamp?: number;
}

export class HistoryStorage {
	#entries: HistoryEntry[] = [];

	add(text: string): void {
		this.#entries.push({ text, timestamp: Date.now() });
	}

	all(): HistoryEntry[] {
		return [...this.#entries];
	}

	search(query: string): HistoryEntry[] {
		return this.#entries.filter(entry => entry.text.includes(query));
	}
}
