# Repository Guidelines

`hexa` is a newly initialized repository: `main` currently has no commits or tracked files. This guide sets expectations for how work should be organized as the project grows. Update it whenever tooling or structure is introduced.

## Project Structure & Module Organization
Establish these conventions as code lands, and keep the tree flat and predictable:
- `src/` — application and library source.
- `tests/` — automated tests, mirroring the `src/` layout.
- `scripts/` — developer utilities and one-off tooling.
- `docs/` — design notes and architecture decisions.
- `assets/` — static images, fixtures, and sample data.

Avoid committing build output, caches, or secrets; add them to `.gitignore` first.

## Build, Test, and Development Commands
No build system exists yet. When you add one, document the exact commands here, for example:
- `make build` — compile or bundle the project.
- `make test` — run the full test suite.
- `npm run dev` / `make run` — start the local development server.

Until then, record setup steps in `README.md` and keep commands reproducible from a clean checkout.

## Coding Style & Naming Conventions
- Use 2-space indentation for markup/config, 4 spaces for Python, and the language default elsewhere.
- Name files and directories `kebab-case`; use `snake_case` for Python symbols and `camelCase` for JS/TS variables.
- Prefer descriptive, full-word identifiers over abbreviations.
- Adopt a formatter and linter with the stack (e.g., `prettier` + `eslint`, or `ruff` + `black`) and run them before committing.

## Testing Guidelines
Add a test framework alongside the first feature (e.g., `pytest`, `jest`). Mirror source paths, name files `test_<module>` or `<module>.test.*`, and require tests for new behavior and bug fixes. Keep the suite green before opening a pull request.

## Commit & Pull Request Guidelines
There is no commit history yet, so adopt [Conventional Commits](https://www.conventionalcommits.org): `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`. Use the imperative mood and keep the subject under 72 characters.

Pull requests should include a short summary, motivation, testing performed, and any linked issues. Add screenshots for UI changes and request review before merging.
