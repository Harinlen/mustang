// @ts-nocheck
export interface ParsedBuiltinSlashCommand {
	name: string;
	args?: string;
}

export interface BuiltinSlashCommandRuntime {
	[key: string]: unknown;
}

export async function executeBuiltinSlashCommand(
	_command: ParsedBuiltinSlashCommand | string,
	_runtime?: BuiltinSlashCommandRuntime,
): Promise<string | undefined> {
	return undefined;
}
