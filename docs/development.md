# Development Workflow

## Branch strategy

Use short-lived feature branches for all work. Keep the default branch aligned with stable, tested code.

Recommended flow:

- `main`: production-ready baseline
- `develop`: integration branch for active development
- feature branches: `feature/<short-name>`
- bugfix branches: `fix/<short-name>`
- hotfix branches: `hotfix/<short-name>`

## Local development

1. Create a feature branch.
2. Keep changes focused and reviewable.
3. Add tests for behavior changes.
4. Run the targeted test suite.
5. Validate syntax and compile checks.
6. Open a pull request against the relevant integration branch.

## Code quality expectations

- type hints for public APIs
- docstrings for public functions and classes
- clear module boundaries
- no broker credentials in code
- no live execution in development branches
- no fake profitability claims or AI execution authority

## Safety gate for future work

Before shipping any new trading-related feature, confirm that:

- live execution remains disabled by default
- deterministic risk enforcement is preserved
- no hidden network calls or broker integration are introduced without explicit review
