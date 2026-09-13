// S-0072/D-3: the dsh half of the hook kind. On the `fs/*` event gate this
// plugin shells out to the hook item's `implement/scope_guard.py` — the same
// script claude's settings.json runs (S-0072/D-2) — so the judgement is one
// implementation both seats invoke, and the seat that refused is a property
// of the role rather than of the harness. `dsh equip` installs this package
// through dsh's own verb and patches its entry in (S-0063/D-17).

import { execFileSync } from "node:child_process";
import { relative } from "node:path";
import { FsError } from "@deepseek-ai/dsh-fs";

export const name = "scope-guard-policy";

function judge(target) {
	// Forgiving the way the script itself is: no mount, no rope. python3 is in
	// the base image; anything unexpected passes rather than wedging the seat
	// on a refusal the script would not have given itself.
	const mount = process.env.TORVE_EQUIPMENT;
	if (!mount) return "";

	const path = relative(process.cwd(), target.displayPath);
	try {
		execFileSync("python3", [mount + "/implement/scope_guard.py"], {
			input: JSON.stringify({ tool_input: { file_path: path } }),
			encoding: "utf-8",
		});
		return "";
	} catch (err) {
		// Only the guard's own words on stderr block a write. `FS_SANDBOX_DENIED`
		// is the one error code that means "an authority refused this mutation";
		// the tool layer maps it to a refusal the model can see and act on.
		return String(err.stderr || "").trim();
	}
}

export function apply(ctx) {
	for (const event of ["fs/write-intent", "fs/edit-intent"]) {
		ctx.on(
			event,
			(target, _actor, next) => {
				const reason = judge(target);
				if (reason) throw new FsError(reason, "FS_SANDBOX_DENIED");
				return next();
			},
			{ prepend: true },
		);
	}
}
