// -nocheck
export class ExtensionUiController {
	constructor(..._args: unknown[]) {}
	dispose(): void {}
	clearExtensionTerminalInputListeners(): void {}
	clearHookWidgets(): void {}
	initializeHookRunner(..._args: unknown[]): void {}
	createBackgroundUiContext(): Record<string, unknown> { return {}; }
	showHookConfirm(..._args: unknown[]): Promise<boolean> { return Promise.resolve(false); }
	initHooksAndCustomTools(): Promise<void> { return Promise.resolve(); }
	emitCustomToolSessionEvent(..._args: unknown[]): Promise<void> { return Promise.resolve(); }
	setHookWidget(..._args: unknown[]): void {}
	setHookStatus(..._args: unknown[]): void {}
	showHookSelector(..._args: unknown[]): Promise<string | undefined> { return Promise.resolve(undefined); }
	hideHookSelector(): void {}
	showHookInput(..._args: unknown[]): Promise<string | undefined> { return Promise.resolve(undefined); }
	hideHookInput(): void {}
	showHookEditor(..._args: unknown[]): Promise<string | undefined> { return Promise.resolve(undefined); }
	hideHookEditor(): void {}
	showHookNotify(..._args: unknown[]): void {}
	showHookCustom(..._args: unknown[]): Promise<unknown> { return Promise.resolve(undefined); }
	showExtensionError(..._args: unknown[]): void {}
	showToolError(..._args: unknown[]): void {}
}
