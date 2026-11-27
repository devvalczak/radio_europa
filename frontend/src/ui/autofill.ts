import { api } from "@jarvis/sdk";
import { toast } from "./index";

interface TaskOut {
  title: string; notes: string; due: string | null; all_day: boolean; priority: string; duration_minutes: number | null;
  remind_minutes: number | null; repeat: string | null;
}
interface AutofillResult { changes: string[]; before?: TaskOut; task?: TaskOut }

const durText = (m: number) => (m % 60 === 0 ? `${m / 60} h` : m > 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`);
const PRI: Record<string, string> = { high: "wysoki", low: "niski", normal: "normalny" };

/** A small chore the user does not have to do: after a task is added without a priority or a length, the AI (when it is
    configured) suggests them. The server fills only what is still default and answers nothing when AI is off; the toast
    says what was filled and can undo it. */
export async function autofillTask(id: number, onChanged?: () => void): Promise<void> {
  let r: AutofillResult;
  try {
    r = await api<AutofillResult>(`/m/tasks/${id}/autofill`, { method: "POST", body: {} });
  } catch {
    return; // a bonus: never an error message
  }
  if (!r.changes.length || !r.before || !r.task) return;
  const parts = [
    r.changes.includes("priority") ? `priorytet ${PRI[r.task.priority]}` : "",
    r.changes.includes("duration") && r.task.duration_minutes ? `czas ok. ${durText(r.task.duration_minutes)}` : "",
  ].filter(Boolean);
  const b = r.before;
  toast(`Uzupełniłem: ${parts.join(", ")}`, {
    action: {
      label: "Cofnij",
      run: () =>
        void api(`/m/tasks/${id}`, {
          method: "PUT",
          body: { title: b.title, notes: b.notes, due: b.due, all_day: b.all_day, priority: b.priority, duration_minutes: b.duration_minutes,
            remind_minutes: b.remind_minutes, repeat: b.repeat, tz: Intl.DateTimeFormat().resolvedOptions().timeZone },
        }).then(() => onChanged?.()),
    },
  });
  onChanged?.();
}
