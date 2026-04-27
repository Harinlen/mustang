import { CombinedAutocompleteProvider, type SlashCommand } from "../src/active-port/tui/autocomplete.js";
import { KeybindingsManager } from "../src/active-port/coding-agent/config/keybindings.js";
import { createPromptActionAutocompleteProvider } from "../src/active-port/coding-agent/modes/prompt-action-autocomplete.js";
import { assert } from "./helpers.js";

const commands: SlashCommand[] = [
	{ name: "help", description: "Show available commands" },
	{ name: "model", description: "Show or switch model" },
	{ name: "plan", description: "Enter, exit, or inspect plan mode" },
	{ name: "compact", description: "Compact conversation context" },
	{ name: "session", description: "List, resume, or delete sessions" },
	{ name: "cost", description: "Show usage and cost" },
	{ name: "memory", description: "List, show, or delete memories" },
	{ name: "cron", description: "Manage scheduled tasks" },
	{ name: "auth", description: "Manage secrets and auth values" },
	{ name: "quit", description: "Exit Mustang CLI" },
	{ name: "exit", description: "Exit Mustang CLI" },
];

const provider = new CombinedAutocompleteProvider(commands);

const all = await provider.getSuggestions(["/"], 0, 1);
assert(all !== null, "expected slash command suggestions");
assert(
	all.items.map(item => item.value).join(",") ===
		"auth,compact,cost,cron,exit,help,memory,model,plan,quit,session",
	"slash commands should be alphabetized when scores tie",
);

const m = await provider.getSuggestions(["/m"], 0, 2);
assert(m !== null, "expected filtered slash command suggestions");
assert(
	m.items.slice(0, 2).map(item => item.value).join(",") === "memory,model",
	"filtered slash commands should keep alphabetical tie-breaks",
);

const c = await provider.getSuggestions(["/c"], 0, 2);
assert(c !== null, "expected /c slash command suggestions");
assert(
	c.items.map(item => item.value).join(",") === "compact,cost,cron",
	"slash command filtering should match command names only",
);

let copiedPrompt = false;
const promptActionProvider = createPromptActionAutocompleteProvider({
	commands,
	basePath: process.cwd(),
	keybindings: KeybindingsManager.inMemory(),
	copyCurrentLine: () => {},
	copyPrompt: () => {
		copiedPrompt = true;
	},
	undo: () => {},
	moveCursorToMessageEnd: () => {},
	moveCursorToMessageStart: () => {},
	moveCursorToLineStart: () => {},
	moveCursorToLineEnd: () => {},
});

const promptActions = await promptActionProvider.getSuggestions(["#"], 0, 1);
assert(promptActions !== null, "expected prompt action suggestions");
assert(
	promptActions.items.map(item => item.value).includes("Copy whole prompt"),
	"prompt actions should include oh-my-pi copy prompt action",
);

const copyPromptAction = promptActions.items.find(item => item.value === "Copy whole prompt");
assert(copyPromptAction !== undefined, "expected copy prompt action");
const applied = promptActionProvider.applyCompletion(["hello #"], 0, 7, copyPromptAction!, "#");
assert(applied.lines.join("\n") === "hello ", "prompt action completion should remove # trigger text");
applied.onApplied?.();
assert(copiedPrompt, "prompt action should execute selected action");

console.log("PASS: autocomplete sort");
