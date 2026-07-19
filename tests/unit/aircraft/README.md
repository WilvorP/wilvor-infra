# Aircraft Unit Tests

This suite tests aircraft code without calling AWS or OpenSky.

Covered components:

- Shared OpenSky mapper and validation
- Bad-record construction
- CloudWatch EMF metric generation
- OpenSky poller helper and handler behavior
- Aircraft raw processor envelope, archive, publishing, and handler behavior
- Aircraft current-state writer validation, DynamoDB conversion, stale-write handling, and handler behavior

AWS clients are replaced with fakes. HTTP calls are replaced with local response objects.

## Install test dependencies

From the repository root:

```powershell
python -m pip install -r requirements-test.txt
```

## Run aircraft unit tests

```powershell
python -m pytest .\tests\unit\aircraft -v
```

Or use the test script:

```powershell
.\scripts\run-unit-tests.ps1 -Target aircraft
```

## Run with coverage

```powershell
.\scripts\run-unit-tests.ps1 `
    -Target aircraft `
    -Coverage
```

Generated reports are written to:

```text
test-results/unit/
├── aircraft-results.xml
└── aircraft-coverage.xml
```

## Unit-test rule

These tests must pass even when:

- Terraform infrastructure is destroyed
- AWS SSO is logged out
- OpenSky is unavailable
- NOAA is unavailable
- The machine has no internet connection
