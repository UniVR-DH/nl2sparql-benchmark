# Instructions for Automated Agents

Mandatory guidelines for any automated agent contributing to this repository.
Primary goals: **no data loss, no broken code, consistent workflow, minimal noise.**

---

## 0. Golden Rules (Non-Negotiable)

* **Do not move files** outside the project root unless explicitly instructed
  → If unclear: ask *what*, *where*, *why*

* **Never overwrite files blindly**

  * Check `git status`
  * Ensure changes are committed or backed up

* **Always inspect before acting**

  * List directories before creating/moving files
  * Do not assume paths

* **No `/tmp` usage**

  * Use project-local dirs (`.temp/`, `output/`) unless explicitly approved

* **Use patch-based edits**

  * Especially for refactors/import changes
  * Avoid breaking references

* **Ask after major changes**

  * Do not dump explanations
  * Prompt: *“Any issues? Want me to run tests?”*

* **Always review before commit**

  * `git status`
  * `git diff`

---

## 1. Communication Style

* Short, structured, no verbosity
* Prefer bullets over paragraphs
* Do not explain unless asked
* After significant changes:

  * Ask for feedback instead of explaining everything
* Offer detail on demand only

**PR signature (mandatory):**
`PR text authored by <agent-name> (<model-version>).`

---

## 2. Commit Policy

### Identity

* Ensure git user/email is configured before committing
* If missing → stop and ask

### Format

* All commits must include:
  **`[BOT] <short summary>`**

### Frequency

Commit after every logical unit:

* Feature / refactor
* Config change
* Docs update
* Formatting / lint changes (including pre-commit fixes)
* Bugfix / tests

### Pre-commit checks

Before changes:

* Check uncommitted changes in:

  * `.pre-commit-config.yaml`
  * `pyproject.toml`
* Ensure no syntax errors in:

  * YAML / JSON / Python

### Workflow rules

* Use non-interactive git:

  * `git rebase --continue --no-edit`
* Review changes before commit:

  * `git status`
  * `git diff`

### Commit organization

* Separate by concern:

  * code / docs / tests
* Large changes → group logically

### File hygiene

* Remove obsolete files completely
* Do **not** leave:

  * shims
  * wrappers
  * dead code
* No backward compatibility unless explicitly requested

### Docs policy

* Only commit generated docs when intentionally updated

### Tags

If tag push fails:

```bash
git tag -d <tag>
git fetch --tags
```

---

## 3. Development Environment

### Core setup

* Python: `>=3.10,<4.0`
* Package manager: **`uv` only**
* Install:

```bash
make install
```

### Virtual environment

Always use `.venv`:

```bash
uv run <command>
```

---

### Dependency Management (Critical)

**Never use:**

* `pip install`
* `pip freeze`

**Always use:**

```bash
uv sync
uv add <package>
uv remove <package>
uv pip list
```

### Missing dependency workflow

1. Check `pyproject.toml`
2. Add if missing:

   ```bash
   uv add <package>
   ```
3. Sync:

   ```bash
   uv sync
   ```

---

### Common Commands

```bash
make install   # setup
uv sync        # sync deps
make check     # lint
make test      # tests
make docs      # docs
make build     # build
```

---

### Docker (Cross-Platform)

For amd64-only images (e.g., `stain/jena` on Apple Silicon):

```bash
export DOCKER_DEFAULT_PLATFORM=linux/amd64
```

---

### TTL Validation (Standard Command)

```bash
docker run --rm \
  --platform=linux/amd64 \
  -v "$PWD":/data \
  stain/jena \
  bash -euo pipefail -c '
    status=0
    while IFS= read -r -d "" f; do
      echo "==== Validating: $f ===="
      if ! riot --validate "$f"; then
        echo "ERROR in $f" >&2
        status=1
      fi
    done < <(find /data/graphs -type f -name "*.ttl" -print0)
    exit $status
  ' 2>&1 | tee ttl-validation.log
```

---

## 4. Bash Best Practices

* Prefer **readable scripts over one-liners**
* One logical action per line
* Avoid chaining (`&&`) unless necessary

### Execution discipline

* Plan first if multiple steps
* Verify command availability before use

### Safety

* Never use `sudo`
* Ask before destructive actions
* Use safe flags (`--dry-run`, `-i`) when possible

### File handling

* Do not write outside project
* Use `mktemp` if needed:

```bash
tmpfile=$(mktemp)
```

### Simplicity

* Avoid complex `awk`
* Prefer `grep`, simple scripts

### Debuggability

* Use intermediate outputs instead of deep pipes

---

## 5. Project Philosophy

* Keep structure clean and minimal
* Avoid assumptions
* Prefer explicit over implicit
* Optimize for maintainability, not cleverness

---

If anything is unclear → **ask before acting**.
