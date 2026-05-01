---
trigger: always_on
---

# cuhibot Project Rules — Always On

These rules apply to every agent response in this workspace.
No exceptions. No skipping. No partial compliance.

---

## RULE 1 — SPLIT ALL LARGE WRITES
Never write more than 400 lines in a single write_to_file call.
Split into: _part1.py, _part2.py, _partN.py
Never ask permission. Just split.
Always write _manifest.json after every part.

---

## RULE 2 — MANIFEST IS MANDATORY
After every part written, update _manifest.json:

{
  "task": "[task name]",
  "workspace": "cuhibot",
  "original_model": "[first model]",
  "current_model": "[current model]",
  "total_parts": N,
  "parts_completed": [list],
  "stopped_at": "[function name or null]",
  "next_part": N,
  "next_function": "[name]",
  "status": "in_progress or complete",
  "style_notes": "[patterns next model must follow]"
}

Never finish a session without updating this file.
This file is what allows any model to take over at any point.

---

## RULE 3 — NO HALLUCINATION EVER
Never write:
- # TODO, # add logic here, # implement this, # placeholder
- pass as a function body
- ... as a placeholder
- "rest follows same pattern"
- "similar to above"
- invented method or library names

If you cannot finish a function, finish it partially
and stop cleanly at its closing line.
Never leave a half-open function body.

---

## RULE 4 — CLEAN STOP BEFORE QUOTA HIT
When your response is getting long and the task is not done:
1. Finish the current function completely
2. Stop before the next function starts
3. Update _manifest.json
4. Output:

═══════════════════════════════════
STOPPING CLEANLY
Written        : [what was done]
Stopped before : [next function name]
Type /cuhi-workspace to continue
═══════════════════════════════════

Never push through quota. A clean stop is always better
than broken code caused by token pressure.

---

## RULE 5 — RESUMING AFTER INTERRUPTION
When you detect the previous session was interrupted:
- _manifest.json exists and status is "in_progress"
- User says "continue" or "/cuhi-workspace"
- Last message ends with "STOPPING CLEANLY"

Do this immediately:
1. Read _manifest.json
2. Read all existing part files
3. Output the handoff announcement
4. Continue from stopped_at function
5. Never rewrite existing code

---

## RULE 6 — MODEL HANDOFF STANDARD
When a new model takes over in this workspace:
- Read _manifest.json first
- Read all part files to understand the existing style
- Match bot.py handler structure exactly
- Match database call patterns exactly
- Match admin check patterns exactly
- Match async/await patterns exactly
- Match logging patterns exactly
- Do not introduce any new pattern not already in the codebase

Output before writing anything:
"═══════════════════════════════
 MODEL HANDOFF — [your model]
 Took over from : [previous model]
 Task           : [task]
 Resuming Part  : [N]
 Style matched  : YES
═══════════════════════════════"

---

## RULE 7 — DEEP BUG FIXING
When fixing bugs, never skip any severity level.
Always audit the full file before touching anything.

Audit format:
[CRITICAL] Line N — root cause: [one line]
[MODERATE] Line N — root cause: [one line]
[MINOR]    Line N — root cause: [one line]

Fix order: CRITICAL first, then MODERATE, then MINOR.
Verify every fix with py_compile.

End every fix session with:
BUGS FOUND    : N  (CRITICAL: N | MODERATE: N | MINOR: N)
BUGS FIXED    : N
VERIFIED      : YES / NO
REMAINING     : [list or NONE]

---

## RULE 8 — THIS WORKSPACE FILE STRUCTURE
Always respect this structure:

bot.py              — final combined bot file
_part1.py           — part 1 of current task
_partN.py           — part N of current task
_manifest.json      — handoff contract between models
combine.py          — merges all parts into bot.py
_fix_report.md      — bug fix session reports
requirements.txt    — dependencies

Never write task code directly into bot.py during a split task.
Always write to _partN.py files.
Only write to bot.py via combine.py after all parts are complete.

---

## RULE 9 — MODEL PRIORITY AND QUOTA AWARENESS
Never hardcode quota refresh times or credit counts.
These change constantly and hardcoded values will be wrong.

PRIORITY ORDER (fixed, does not change):
1. Claude Opus 4.6 (Thinking)     — highest quality, use first
2. Claude Sonnet 4.6 (Thinking)   — first fallback
3. GPT-OSS 120B (Medium)          — second fallback
4. Gemini 3.1 Pro (High)          — third fallback
5. Gemini 3.1 Pro (Low)           — fourth fallback
6. Gemini 3 Flash                 — last resort only
7. AI Credits                     — absolute final fallback

QUOTA CHECK RULE:
Never assume a model has quota remaining.
Never assume a model is exhausted.
The user manages quota. The agent manages code quality.

When a model switch happens, the user will tell you:
- Which model is now active
- Whether to continue or start fresh

When you are the active model and sense quota pressure:
1. Stop cleanly at a function boundary
2. Update _manifest.json
3. Output:

═══════════════════════════════════
QUOTA WARNING — STOPPING CLEANLY
Current model  : [your model name]
Written        : [what was done this session]
Stopped before : [next function name]
Next step      : User switches to next available model
Type /cuhi-workspace to resume
═══════════════════════════════════

Do not tell the user which model to switch to.
Do not guess which models have quota remaining.
The user checks quota in Antigravity settings and decides.
Your only job is to stop cleanly and leave a good manifest.

AI CREDITS RULE:
If the user tells you AI Credits are active:
- Reduce part size to 200 lines maximum
- Add to manifest: "running_on": "ai_credits"
- Output at start of each part:
  "AI CREDITS ACTIVE — part size reduced to 200 lines"
- Never assume how many credits remain
- The user will tell you when credits are low
---

## RULE 10 — FINAL ASSEMBLY CHECKLIST
When all parts are complete, before touching bot.py:
- combine.py merges all parts in correct order
- py_compile passes with zero errors
- Every command handler present: /start /help /status and others
- Every database function present
- Every admin function present
- Every callback handler present
- No duplicate function names
- No missing imports

Only after all checks pass, write the final bot.py.