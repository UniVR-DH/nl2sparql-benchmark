# Instructions for Automated Agents

These are to be followed religiously by any automated agents (like GitHub Copilot) that are contributing to the codebase. 
They are fundamental to avoid: data loss, code, loss,  bugs, developer confusion.
The goal is to maintain a consistent communication style and ensure that all contributions are clear and concise.

---

## Table of Contents
- [0. Agent Communication Style](#0-agent-communication-style)
- [1. Commit Policy](#1-commit-policy)
- [2. Development Environment](#2-development-environment)
- [3. Bash Best Practices for Copilot](#3-bash-best-practices-for-copilot)
- [4. Project Overview](#4-project-overview)  

## GOLDEN RULES:

- **Never move files out of the project root** without explicit user instruction, and even in that case ask for permission and exactly which files to move and where, rather than making assumptions
- **Never use `cat` or overwrite files** if they have not been backed up or committed, to avoid data loss. Always check for uncommitted changes before modifying files directly.
- **Always use the patch-based approach** for refactoring imports and moving files, to ensure that all references are updated correctly and to avoid broken imports.
- **Always ask for feedback** after major changes instead of preemptively listing all details
- **Always review and follow the Commit Policy below** and use `git status` and `git diff` before committing to ensure that the commit message accurately reflects the changes made.

## 0. Agent Communication Style

- **Keep summaries SHORT** - Use bullet points, no verbose explanations
- **After major changes:** Ask about possible issues rather than listing details
- **Offer details on request** - "Need more details?" instead of preemptive walls of text
- **Question first** - After refactoring/big changes, ask: "Any issues? Want me to run tests?"
- **PR Text Signature:** Always append a final line in PR descriptions that identifies the agent and model version. Format: `PR text authored by <agent-name> (<model-version>).`

## 1. Commit Policy

- **Virtual Environment:** Always run all commands, scripts, and hooks inside the project Python virtual environment (`.venv`). Activate the venv before running any Python, pip, or tool commands using `uv`.

- **Pre-Commit Checks:** Before making any new changes, always check for:
  - Unstaged or uncommitted changes to crucial config files (especially `.pre-commit-config.yaml`, `pyproject.toml`)
  - Syntax errors in crucial files (YAML, Python, JSON, etc.) to avoid cascading failures

- **Non-Interactive Commands:** When scripting or automating rebases, always use `git rebase --continue --no-edit` to avoid opening an interactive editor (e.g., vim) for commit messages. This ensures a fully non-interactive workflow.

- **Commit Frequency:** Agents must always commit their changes after every significant or logical update. This includes:
  - Any new feature, refactor, or config update
  - Any documentation update or reorganization
  - Any formatting or linting pass (applied by pre-commit hooks)
  - Any bugfix or test addition
  - Any time a pre-commit hook or tool modifies a file, the agent is responsible for staging and committing the result, even if it is a whitespace or formatting fix

- **Documentation Commits:** API documentation (using mkdocs) is generated manually (not by pre-commit). Only commit docs when intentionally regenerated (e.g., before a release or after major code changes).

- **Commit Organization:** Group committed files by topic and the extent of the change:
  - If a change affects multiple topics (e.g., docs and code), prefer separate commits for each topic
  - If a change is extensive (touches many files), group by logical area (e.g., all doc files, all API code, all test files, etc.)

- **Commit Message Format:** Every commit created by an automated agent must include the flag `[BOT]` in the commit title (recommended prefix: `[BOT] <summary>`).

- **Pull Request Disclaimer:** Every PR created or updated by an automated agent must end with a disclaimer line identifying both agent and model version. Required format:
  - `PR text authored by <agent-name> (<model-version>).`
  - Example: `PR text authored by GitHub Copilot (GPT-5.3-Codex).`

- **Check Git Status and Diff:** Before committing, always run `git status` and `git diff` to review changes. Use this information to craft an accurate and descriptive commit message.

- **File Cleanup Policy:** When refactoring or consolidating modules:
  - **Never leave re-export shims or compatibility wrappers** unless explicitly instructed by the user
  - **Delete deprecated/obsolete files completely** rather than leaving empty placeholders
  - Default behavior is to keep the codebase clean and force migration to new import paths
  - If backward compatibility is required, the user will explicitly request it
  - Always remove unused files, dead code, and temporary artifacts

- **Git Tag Management:** To avoid conflicts when pushing tags:
  - If `git push --tags` fails with "already exists" errors for certain tags, delete conflicting local tags and re-fetch from remote:
    ```bash
    git tag -d <tag1> <tag2>  # Delete local tags that conflict
    git fetch --tags          # Re-fetch all tags from remote
    ```
  - This ensures local tags match remote tags and prevents repeated push failures
  - Alternative: Use `git push origin <specific-tag>` to push only new tags individually

---

## 2. Development Environment

- **Package Manager:** This project uses `uv` (not poetry or pip) for dependency management
- **Python Version:** Requires Python >=3.10, <4.0
- **Installation:** Run `make install` to set up the environment and pre-commit hooks
- **Virtual Environment:** Always activate `.venv` before running any commands

### Dependency Management with uv (CRITICAL)

**NEVER use `pip install` or `pip freeze` for this project. ALWAYS use `uv`.**

- **Sync Environment:** `uv sync` - Install all dependencies from `pyproject.toml` and `uv.lock`
- **Add Dependency:** `uv add <package>` - Add a new package to dependencies in `pyproject.toml`
- **Remove Dependency:** `uv remove <package>` - Remove a package from `pyproject.toml`
- **Check Installation:** `uv pip list` or `pip show <package>` - Verify a package is installed
- **Run Commands in venv:** `uv run <command>` - Execute commands in the virtual environment

### When Dependencies Are Missing:
1. Check `pyproject.toml` dependencies section - is the package listed?
2. If missing, add it with `uv add <package>` (optionally specify version constraints)
3. Run `uv sync` to install everything
4. Do NOT run `pip install`

### Docker Usage (Optional)

If you are on macOS Apple Silicon and run amd64-only images (for example `stain/jena`), set:

```bash
export DOCKER_DEFAULT_PLATFORM=linux/amd64
```

### Common Commands:
```bash
# Install environment and pre-commit hooks
make install

# Sync venv after pulling changes with new dependencies
uv sync

# Add a new dependency
uv add structlog>=24.1.0

# Remove a dependency
uv remove uvicorn

# Run code quality checks
make check

# Run tests
make test

# Build documentation and serve locally
make docs

# Build wheel file
make build
```

---

## 3. Bash Best Practices for Copilot

- **Limit to one logical action per line**; avoid multi-step one-liners and `&&` chaining unless explicitly requested. 

- Break workflows into **numbered steps** instead of chaining

- Optimize for **clarity and debuggability**, not brevity

- If **multiple commands are required**, first **make and communicate a plan** and **check availability of all commands**; ask if any are missing before proceeding

- **Never use `sudo`**

- **Avoid complex `awk` scripts**; prefer simpler tools or small scripts
  Example: `grep "pattern" file.txt  # simpler than long awk one-liner`

- **Do not assume commands or flags exist**; verify syntax for the current Bash environment
  Example:

  ```bash
  if command -v curl >/dev/null; then
      curl http://example.com
  else
      echo "curl not available"
  fi
  ```

- **Never create files outside the current directory manually**; if temp files are needed, use `mktemp`
  Example:

  ```bash
  tmpfile=$(mktemp)
  echo "data" > "$tmpfile"

  tmpdir=$(mktemp -d)
  # use $tmpdir safely
  ```

- **If Docker is needed**, verify the daemon is running, check the host architecture (`uname -m`), and pull the appropriate image tag for the platform (e.g., `arm64` for Apple Silicon, `amd64` for Intel); set the target architecture via `export DOCKER_DEFAULT_PLATFORM=linux/arm64` or `linux/amd64` accordingly

- Ask for confirmation before **destructive or state-changing commands**

- Use **clear intermediate outputs** instead of piping everything
  Example:

  ```bash
  ls -l > files.txt
  cat files.txt
  ```

- Default to **readable scripts over dense one-liners**

- Include **brief inline comments** for non-trivial commands

- Prefer **safe flags** (`-i`, `--dry-run`, `--preview`) when available


## 4. Project Overview

This repository is expected to evolve. Agents should preserve the core intent and milestones without enforcing overly rigid structure.

### Stable Milestones (Commit-Relevant)

- `README.md` is a key entry point and should stay aligned with the current repository layout and conventions.
- `graphs/` is a core dataset area and should remain clearly organized.
- The paired `.graph` / `.ttl` naming idea is a project convention and should be kept consistent when adding new resources. For example, if a new dataset `example` is added, it should be in a subfolder and have both `example.ttl` and `example.graph` files in the same folder.
- `.external/` contains vendored external vocabularies used by the project and is part of the committed repository state.
- `GUIDELINE.md` is the canonical modeling guidance and should stay synchronized with actual encoding practice.

### Flexibility Principle

- Prefer guidance over strict enforcement: adapt to new files/folders as the project grows, while keeping naming and documentation coherent.
- If structure changes are proposed, update `README.md` and `GUIDELINE.md` in the same change set when relevant.

### Local Temporary Data Policy

- `.temp/` is local-only working space.
- `.temp/` must always be ignored by git (`.gitignore`).
- Never reference `.temp/` in committed files (docs, examples, instructions, or data files).