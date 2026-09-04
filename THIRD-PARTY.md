# Third-party notices

## skills.sh Agent table

`agenthub/agents.json` derives from
[vercel-labs/skills, README Agent table](https://github.com/vercel-labs/skills/blob/435076e78988e1e6ec40d00b0b1d76bdbbc5419a/README.md#supported-agents),
commit `435076e78988e1e6ec40d00b0b1d76bdbbc5419a`.

Run `python3 tools/refresh_agents.py` to reproduce this version. Use `--ref COMMIT`
to fetch another version, review the resulting changes, then update the pinned
commit in the script and this notice. `--source PATH` reads a local `README.md`.
The script keeps local instruction paths, detection paths, and environment
variables when their defaults match the README. Initial detection paths and
environment variables come from `src/agents.ts` at the same commit. Review
detection and instruction paths when an Agent is added. The script reads
upstream data without executing upstream code.

The App adds global instruction paths for Claude Code and Codex. Other global
instruction paths are unknown and remain null. Set a path in `hub.toml` to add it.
Codex uses `~/.agents/skills` as required by the v2 specification. An Agent is
Universal here only if its global skills path is `~/.agents/skills`, not merely
when its project path is `.agents/skills`.

Detection uses home paths, not the working directory or application bundles.
Project-only Agents remain available for explicit selection. Zed detection uses
its XDG configuration path. OpenClaw uses its current `.openclaw/skills` path;
use a `hub.toml` override for an older `.clawdbot` or `.moltbot` installation.

The upstream table is distributed under the MIT License:

```text
MIT License

Copyright (c) 2026 Vercel, Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
