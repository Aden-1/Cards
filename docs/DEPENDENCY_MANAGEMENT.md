# Dependency management and supply-chain controls

## Source of truth

`requirements.in` is the reviewed runtime dependency input. `requirements-dev.in`
adds development and supply-chain tooling. The generated `requirements.txt` and
`requirements-dev.txt` are fully resolved lockfiles for Python 3.13 and are the
only requirement files installed by CI or deployment environments.

Do not hand-edit either generated lockfile. Review changes to the `.in` file and
the resulting lockfile together. Direct application dependency versions are
intentionally retained from the prior compatible set; transitive additions are
resolved only to make the former partial list complete.

## Reproducible regeneration

Use a clean Python 3.13 virtual environment and the pinned compiler from the
development input:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install pip-tools==7.5.1
pip-compile --resolver=backtracking --allow-unsafe --generate-hashes --output-file requirements.txt requirements.in
pip-compile --resolver=backtracking --allow-unsafe --generate-hashes --output-file requirements-dev.txt requirements-dev.in
python -m pip install --require-hashes -r requirements-dev.txt
python -m pip check
python -m pip_audit --require-hashes -r requirements.txt
```

The compiler runs under Python 3.13 so environment markers and artifact hashes
are produced for the supported interpreter. CI verifies installation with
`--require-hashes`; a lockfile change that is not complete or has an incorrect
hash fails before tests run.

## SBOM, audit, and license policy

CI strictly audits the checked-in production lockfile, not the ambient virtual
environment, using `pip-audit --require-hashes -r requirements.txt`. The same
locked production set is emitted as a CycloneDX JSON SBOM artifact.

The application is GPL-3.0. Runtime dependency licenses must therefore be
GPL-3.0-compatible. The automated policy allows MIT, BSD-2/3-Clause, Apache-2.0,
PSF-2.0, GPL-3.0-only/or-later, MPL-2.0, and LGPL-3.0-only/or-later. LGPL-3.0 is
explicitly allowed for `psycopg` and `psycopg-binary`; it is weak copyleft and is
compatible with this GPL-3.0 application. GPL-2.0-only, AGPL, proprietary, and
unknown licenses are denied. Any new license form needs an explicit policy review
and a matching update to `scripts/check_dependency_licenses.py` before it can be
merged.

The policy script filters the `pip-licenses` inventory to packages in
`requirements.txt`, so development-only tooling cannot mask or broaden the
production review scope.

## Update process

Dependabot proposes Python and GitHub Actions updates weekly. Review each pull
request for release notes, compatibility, vulnerabilities, license changes, and
the regenerated hashes. Do not accept an update solely because it is available.
