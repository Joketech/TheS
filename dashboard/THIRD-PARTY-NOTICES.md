# Third-party notices and dependency boundaries

- The application uses the separately installed Python standard library (including SQLite bindings and zoneinfo). Python and SQLite are not vendored here; their respective upstream terms continue to apply.
- Optional native agent runtime/SDK and Chromium are not bundled, downloaded, installed or licensed by this repository. Obtain, review and configure them separately under their own terms; the required compatible interfaces are documented in README.
- The frontend is the sanitized existing dashboard's vanilla HTML/CSS/JavaScript and inline canvas/SVG implementation, plus new local brand binding and a generic local mark. No third-party JavaScript framework, web font, image library or remote asset is bundled or fetched by these new assets.
- Existing source notices/docstrings retained in the code do not grant the owner's code/design rights. No project-level open-source licence was approved. Preserve any additional applicable upstream notice identified during MIRA's independent review before publication.
