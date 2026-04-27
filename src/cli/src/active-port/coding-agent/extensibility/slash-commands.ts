// -nocheck
import type { SlashCommand } from "@oh-my-pi/pi-tui";

export const BUILTIN_SLASH_COMMANDS: SlashCommand[] = [];

export async function loadSlashCommands(): Promise<SlashCommand[]> {
	return BUILTIN_SLASH_COMMANDS;
}
