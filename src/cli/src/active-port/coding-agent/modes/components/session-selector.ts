import { type Component } from "@oh-my-pi/pi-tui";
import { renderSessionPicker, createPickerState } from "@/sessions/picker.js";
import type { CliSessionInfo } from "@/sessions/types.js";

/**
 * Mustang adapter for the upstream session selector surface.
 *
 * The upstream component is file-storage oriented; this adapter renders the
 * same session selection shape from ACP summaries and leaves lifecycle actions
 * to SessionService callers.
 */
export class SessionSelectorComponent implements Component {
  constructor(
    private sessions: CliSessionInfo[],
    private selected = 0,
  ) {}

  setSessions(sessions: CliSessionInfo[]): void {
    this.sessions = sessions;
    this.selected = Math.min(this.selected, Math.max(0, sessions.length - 1));
  }

  setSelected(selected: number): void {
    this.selected = Math.max(0, Math.min(selected, Math.max(0, this.sessions.length - 1)));
  }

  invalidate(): void {}

  render(): string[] {
    const state = createPickerState(this.sessions, 10);
    state.selected = this.selected;
    return renderSessionPicker(state).split("\n");
  }
}

