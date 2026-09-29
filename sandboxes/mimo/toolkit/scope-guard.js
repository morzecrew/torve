// S-0072/D-3: mimo's half of the hook kind, beside dsh's scope-guard plugin.
// Before a tool writes a file, this shells out to the hook item's
// `implement/scope_guard.py`, the script claude's settings and dsh's plugin
// run (S-0072/D-2), so the judgement is one implementation every seat
// invokes. mimo loads it as a plugin named in its configuration, which
// `provider_config.py` writes when the manifest carries a hook.

import { execFileSync } from "node:child_process";
import { isAbsolute, relative } from "node:path";

// The tools that write one named file; anything else, the shell included, is
// not a declared write and the guard has no claim on it.
const WRITES = new Set(["write", "edit", "multiedit"]);

function judge(directory, file) {
	// Forgiving the way the script is: no mount, no rope. Anything unexpected
	// passes rather than wedging the seat on a refusal the script would not
	// have given itself.
	const mount = process.env.TORVE_EQUIPMENT;
	if (!mount) return "";

	const path = isAbsolute(file) ? relative(directory, file) : file;
	try {
		execFileSync("python3", [mount + "/implement/scope_guard.py"], {
			input: JSON.stringify({ tool_input: { file_path: path } }),
			encoding: "utf-8",
			cwd: directory,
		});
		return "";
	} catch (err) {
		return String(err.stderr || "").trim();
	}
}

export const ScopeGuard = async ({ directory }) => ({
	"tool.execute.before": async (input, output) => {
		if (!WRITES.has(input.tool)) return;
		// mimo's tools name the path `file_path` (measured on 0.1.15); the
		// opencode spelling it forked from is `filePath`.
		const args = output?.args ?? {};
		const file = args.file_path ?? args.filePath;
		if (!file) return;
		const reason = judge(directory, file);
		if (reason) throw new Error(reason);
	},
});
