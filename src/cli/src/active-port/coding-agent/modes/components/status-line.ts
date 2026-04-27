// -nocheck
import type { Component } from "@oh-my-pi/pi-tui";
import { theme } from "../../modes/theme/theme";
import type { AgentSession } from "../../session/agent-session";

export interface StatusLineSettings {
	preset?: string;
	leftSegments?: string[];
	rightSegments?: string[];
	separator?: string;
	showHookStatus?: boolean;
}

export class StatusLineComponent implements Component {
	#mode = "ready";
	#title = "Mustang";
	#model = "";

	constructor(private readonly session?: AgentSession) {}

	updateSettings(_settings: StatusLineSettings): void {}
	setAutoCompactEnabled(_enabled: boolean): void {}
	setSubagentCount(_count: number): void {}
	setSessionStartTime(_time: number): void {}
	setPlanModeStatus(_status: { enabled: boolean; paused: boolean } | undefined): void {}
	setHookStatus(_key: string, _text: string | undefined): void {}
	watchBranch(_onBranchChange: () => void): void {}
	dispose(): void {}
	invalidate(): void {}

	setMode(mode: string): void {
		this.#mode = mode;
	}

	setTitle(title: string): void {
		this.#title = title;
	}

	setModel(model: string): void {
		this.#model = model;
	}

	render(width: number): string[] {
		const model = this.#model || this.session?.agent?.model?.id || "";
		const text = ` ${this.#title} ${theme.sep.dot} ${this.#mode}${model ? `${theme.sep.dot}${model}` : ""} `;
		return [text.padEnd(Math.max(0, width))];
	}
}
