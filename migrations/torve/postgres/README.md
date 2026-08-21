# Torve document tables (tasks_ref, attempts, gate_results, findings,
# review_feedback) arrive with the attempts store; this history starts at the
# first table. Creating the directory early keeps `torve migrate torve` a
# stable no-op rather than an unknown target (D-12.6).
