# sparql-annotator

Minimal implementation of the roadmap M1+M2: annotate SPARQL queries and read/write TTL/CSV/JSON.

Usage (installed in venv):

```
python -m sparql_annotator.cli annotate --input queries.csv --format csv --output annotations.csv
```

Development setup
-----------------

This project uses `uv` to create reproducible development environments from the
lockfile described in the roadmap. Do not create or manage virtualenvs manually
and avoid calling `pip install -e .` or ad-hoc installs.

To prepare your environment (including optional extras such as the endpoint
integration), run the single command below from the package folder:

```bash
cd scripts/sparql-annotator
uv sync --all-extras
```

This will create and populate the environment according to the lockfile; once
complete you can run the CLI or tests inside the environment provided by `uv`.

