---
description: Continue interrupted task in this workspace. Reads manifest, finds last part, resumes from stopped function without rewriting.
---

## /cuhi-workspace — Continue Task in This Workspace

You have been given the /cuhi-workspace command.
A previous agent session was interrupted or hit quota in THIS workspace.
Follow every step below in exact order.

---

### STEP 1 — SCAN THIS WORKSPACE FIRST
Before reading the manifest, scan the workspace and list:
- All _partN.py files present
- Whether _manifest.json exists
- Whether a combine.py exists
- Current state of bot.py (does it look complete or partial?)
- Any _fix_report.md files with previous bug reports

Output what you find:
"Workspace scan:
 Parts found     : [list]
 Manifest        : exists / not found
 bot.py status   : [complete / partial / missing]
 Fix reports     : [list or none]"

---

### STEP 2 — READ MANIFEST
Read _manifest.json and extract:
- task name
- parts completed
- stopped_at function
- next part number
- which model last wrote
- style_notes for this project

If no manifest, check _fix_report.md for last known state.
If neither exists:
"No manifest or fix report found.
 Tell me: what was being built and where did it stop?"
Then wait.

---

### STEP 3 — READ EXISTING PARTS IN ORDER
Read each part file completely:
_part1.py → _part2.py → ... → last completed part

For this workspace specifically note:
- How bot.py handlers are structured
- How the database calls are made
- How errors are logged
- How admin checks are done
- Async patterns used throughout

You must write new code that is indistinguishable from the existing code.

---

### STEP 4 — ANNOUNCE AND RESUME

═══════════════════════════════════
WORKSPACE RESUME — cuhibot
Task            : [from manifest]
Original model  : [from manifest]
Current model   : [your model]
Parts done      : [list]
Stopped at      : [function name]
Now writing     : Part [N]
Workspace files : bot.py + _partN.py structure
═══════════════════════════════════

Then immediately start writing Part N.
No further confirmation needed unless manifest is missing.

---

### STEP 5 — WRITING RULES FOR THIS WORKSPACE
These rules apply specifically to this cuhibot workspace:

- Max 400 lines per write_to_file call
- All handler functions must be fully implemented
- No placeholder comments anywhere
- All database calls must match the pattern already in existing parts
- All admin checks must use the same pattern as existing parts
- All error handlers must log and not silently pass
- If writing to bot.py directly, read it fully first

---

### STEP 6 — UPDATE MANIFEST AFTER EVERY PART
{
  "task": "[task]",
  "workspace": "cuhibot",
  "original_model": "[model]",
  "current_model": "[your model]",
  "total_parts": N,
  "parts_completed": [list],
  "stopped_at": "[function or null]",
  "next_part": N,
  "next_function": "[name]",
  "status": "in_progress or complete",
  "style_notes": "handler structure, db pattern, admin check pattern"
}

---

### STEP 7 — CLEAN STOP
When approaching output limit:
1. Finish current function completely
2. Stop before next function
3. Update manifest
4. Output:

═══════════════════════════════════
WORKSPACE STOP — cuhibot
Written        : [summary]
Stopped before : [next function]
Next model     : use next available quota
Type /cuhi-workspace to resume
═══════════════════════════════════

---

### STEP 8 — FINAL ASSEMBLY
When all parts complete:
1. Write combine.py — merges all parts into bot.py in correct order
2. Run py_compile on bot.py
3. Check every handler, command, and database function exists by name
4. Update manifest: "status": "complete"
5. Output:

═══════════════════════════════════
cuhibot COMPLETE
Parts merged    : N
Total lines     : N
Handlers found  : [list]
Commands found  : [list]
Compile         : PASS / FAIL
bot.py ready    : YES / NO
═══════════════════════════════════