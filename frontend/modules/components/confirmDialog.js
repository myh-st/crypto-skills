// Confirmation for material actions only (full close, large reduce, risk increase, return to
// autonomous AI, automation changes). Native <dialog> traps focus; focus returns afterwards.
import { escapeHtml } from "../format.js";

const COPY = {
  CONFIRM_CLOSE: { title: "Close PAPER position", confirm: "Close position" },
  CONFIRM_LARGE_REDUCE: { title: "Large PAPER reduce", confirm: "Reduce position" },
  CONFIRM_RISK_INCREASE: { title: "Increase risk-at-stop", confirm: "Accept higher risk" },
  CONFIRM_RETURN_TO_AI: { title: "Return control to AI", confirm: "Return to AI" },
  CONFIRM_AUTOMATION_CHANGE: { title: "Change automation", confirm: "Apply" },
  CONFIRM_REPLAN: { title: "Apply AI re-plan", confirm: "Apply re-plan" },
  CONFIRM_SAFETY_OVERRIDE: { title: "Override execution safety", confirm: "Override and execute" },
  CONFIRM_KILL_SWITCH_LOWER: { title: "Lower the kill switch", confirm: "Lower kill switch" },
  CONFIRM_MATERIAL_CHANGE: { title: "Change the experiment's treatment", confirm: "Save as new version" },
  CONFIRM_MANIFEST_VERSION: { title: "Record a new experiment version", confirm: "Record version" },
};

function detailLines(details = {}) {
  const lines = [];
  const fmt = (value) => (typeof value === "number" ? value.toLocaleString("en-US", { maximumFractionDigits: 6 }) : String(value));
  for (const [key, value] of Object.entries(details)) {
    if (value === null || value === undefined) continue;
    lines.push(`${key.replaceAll("_", " ")}: ${fmt(value)}`);
  }
  return lines;
}

export function confirmAction({ title, message = "", lines = [], confirmLabel = "Confirm", tone = "danger" } = {}) {
  return new Promise((resolve) => {
    const previous = document.activeElement;
    const dialog = document.createElement("dialog");
    dialog.className = "confirm-dialog";
    dialog.setAttribute("aria-labelledby", "confirm-dialog-title");
    dialog.innerHTML = `
      <form method="dialog">
        <h2 id="confirm-dialog-title">${escapeHtml(title)}</h2>
        ${message ? `<p>${escapeHtml(message)}</p>` : ""}
        ${lines.length ? `<ul class="confirm-lines">${lines.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>` : ""}
        <p class="confirm-paper">PAPER simulation only · no real exchange order is sent.</p>
        <div class="confirm-actions">
          <button type="submit" value="cancel" class="btn btn--ghost">Cancel</button>
          <button type="submit" value="confirm" class="btn ${tone === "danger" ? "btn--danger" : "btn--primary"}">${escapeHtml(confirmLabel)}</button>
        </div>
      </form>`;
    document.body.appendChild(dialog);
    dialog.addEventListener("close", () => {
      const ok = dialog.returnValue === "confirm";
      dialog.remove();
      if (previous && typeof previous.focus === "function") previous.focus();
      resolve(ok);
    });
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
      dialog.querySelector('[value="cancel"]').focus();
    } else {
      dialog.remove();
      resolve(window.confirm(`${title}\n${lines.join("\n")}`));
    }
  });
}

// Runs `action(confirmed)`; if the server answers 409 with a confirmation code, asks the user
// with the exact instrument/size details returned by the server and retries once.
export async function withConfirmation(action) {
  try {
    return await action(false);
  } catch (error) {
    if (!error?.confirmation) throw error;
    const copy = COPY[error.confirmation.code] || { title: "Confirm PAPER action", confirm: "Confirm" };
    const ok = await confirmAction({
      title: copy.title,
      message: error.message.replace(/^[A-Z_]+:\s*/, ""),
      lines: detailLines(error.confirmation.details),
      confirmLabel: copy.confirm,
      tone: ["CONFIRM_RETURN_TO_AI", "CONFIRM_KILL_SWITCH_LOWER", "CONFIRM_MANIFEST_VERSION"].includes(error.confirmation.code) ? "primary" : "danger",
    });
    if (!ok) return null;
    return action(true);
  }
}
