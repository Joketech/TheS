# Portable Mission Control dashboard

A full, sanitized, brand-configurable operations dashboard: Overview, Agents, Office, Tasks, Schedule, Chat, Content, CRM and Docs. The original active UI and Python backend/modules are retained—not a reduced demonstration page. The old uploaded static reference is superseded by the sanitized active UI in both template filenames, so stale demo figures are not distributed.

Source prepared for the existing public repository **https://github.com/Joketech/TheS**. The existing root `../LICENSE` is preserved unchanged; see `LICENSE-STATUS.md` and `THIRD-PARTY-NOTICES.md`. Source preparation is not a remote publication, runtime deployment or integration activation. The publication execution path requires separate owner approval coordinated by MIRA; this snapshot supplies no publisher launcher or credentials.

## Requirements
- Python 3.11+ with its standard library, SQLite support and operating-system IANA timezone database. No pip/npm installation is required for the application or Python tests. `requirements.txt` records this dependency contract.
- The tested application environment is Linux/POSIX. Some filesystem protections use POSIX-only facilities such as `os.O_NOFOLLOW`; native Windows application execution is not established by this preparation. The Git bundle itself is transferable; application portability is a separate acceptance question.
- A current browser. Full-screen/orientation behaviour needs actual-device acceptance; desktop phone emulation is not physical iPhone proof.
- Optional: separately configured Chromium for server-side PDF export; a separately installed, compatible native agent runtime for operational integrations. Neither is bundled or downloaded automatically.

## Obtain a fresh copy
The initial source publication is already on public main. Obtain the current published source using an anonymous read-only clone:
```sh
git clone --branch main https://github.com/Joketech/TheS.git brand-dashboard
cd brand-dashboard/dashboard
```
The full application is under `dashboard/`, not the repository root. History and the original root LICENSE remain intact. Cloning source does not deploy the application, configure native integrations or grant authority to send messages. A separately supplied reviewed candidate bundle is a local handoff for acceptance; it is not proof that an unpushed candidate is on remote main.

## Initialize and run — disconnected by default
Use an absolute **new, dedicated** data directory, not a live dashboard or agent profile directory:
```sh
export DATA_DIR="$PWD/data"
export CONTENT_DIR="$DATA_DIR/content"
export BRAND_CONFIG="$PWD/config/brand.json"
export PORT=8181
export REPORTING_TIMEZONE=UTC
export EXTERNAL_RUNTIME_ENABLED=0
export CHAT_SEND_ENABLED=0
python3 -B initializer.py
python3 -B server.py
```
Open `http://127.0.0.1:8181/`; `http://127.0.0.1:8181/template` serves the same full frontend. Canonical source: `templates/dashboard.template.html`; compatible template filename: `the-s-mission-control-template.html`. All three HTML files are intentionally byte-identical. Do not open the HTML with `file://` and mistake failed API requests for a deployed dashboard.

The server binds **127.0.0.1 only**. It does not install a service, start a gateway, register jobs, call AI, provision accounts or discover host profiles. Stop with Ctrl-C. Do not use a live service's port/data root. No model, job, credential or messaging route is inherited.

Fresh defaults: empty board, CRM, documents and local ledgers; external profile activity/schedules/chat/usage are **Unavailable**, not fake idle/connected/zero-cost states. Host hardware readings describe the machine hosting this instance, not a separate server per brand. The UI's decorative city traffic is not real agent activity.

`initializer.py` creates empty instance schemas/directories; the server creates its remaining local board/chat/attachment schema on first start. It does not create owner credentials or CRM signing keys. `.env.example` is a reference, **not auto-loaded**; export only the settings you intend. Timezone configuration is validated before storage initialization; browser display timestamps use browser-local time, while reporting date boundaries use `REPORTING_TIMEZONE`.

## Rebrand
Stop only this dedicated instance with Ctrl-C before changing its configuration. Preserve its `DATA_DIR` and `CONTENT_DIR`. This fictional example reads the shipped public defaults and exclusively creates a **new** public brand file; it refuses to overwrite an existing file:
```sh
python3 - <<'PY'
import json
from pathlib import Path
brand = json.loads(Path('config/brand.json').read_text())
brand.update(name='Aurora Control', company='Aurora Example',
             attribution='Aurora Example', logo_text='AU',
             accent='#1e8f71', accent_soft='#176b56')
with Path('config/aurora.brand.json').open('x') as output:
    json.dump(brand, output, indent=2)
    output.write('\n')
PY
export BRAND_CONFIG="$PWD/config/aurora.brand.json"
python3 -B server.py
```
Keep the exported port, data paths and disconnected flags from initialization. For other changes, back up your own configuration before editing it and retain the following schema:
- `name`, `company`, `attribution`, `logo_text`: public display text.
- `accent`, `accent_soft`: six-digit hex colors.
- `logo_url`: empty for text mark, or a safe local `/assets/...` image path. An example `assets/logo.svg` is included. Remote/data URLs and traversal are rejected by the frontend.
- `agents`: five entries with stable internal IDs `mira`, `sage`, `seline`, `surge`, `spectra`; configurable `label`, `role`, `code`, `initials`. Labels are not authenticated identities and do not provision workers. Retain these internal IDs; this version preserves the five-slot source layout.

`/api/brand` exposes only this public display configuration. **Never put secrets, email addresses, routing destinations or private business records in branding.** Names render as text, not executable HTML. Changing colors/labels does not change API roles or authorization. Header protocol names retain their original identifiers for compatibility.

## Two independent brands
Use two extracted/copied repositories, distinct absolute data roots and distinct loopback ports. In separate terminals:
```sh
# In the first repository copy:
export DATA_DIR="$PWD/data" CONTENT_DIR="$PWD/data/content" BRAND_CONFIG="$PWD/config/brand.json"
export PORT=8181 EXTERNAL_RUNTIME_ENABLED=0 CHAT_SEND_ENABLED=0 REPORTING_TIMEZONE=UTC
python3 -B initializer.py
python3 -B server.py
# In the second repository copy (with its own edited brand.json):
export DATA_DIR="$PWD/data" CONTENT_DIR="$PWD/data/content" BRAND_CONFIG="$PWD/config/brand.json"
export PORT=8182 EXTERNAL_RUNTIME_ENABLED=0 CHAT_SEND_ENABLED=0 REPORTING_TIMEZONE=UTC
python3 -B initializer.py
python3 -B server.py
```
Never share `DATA_DIR`, credentials, runtime profiles, attachment roots or deployment secrets. Separate ports alone do not provide storage or security isolation. Browser cookies are host/path-scoped, not port-scoped: use separate browser profiles or separately reviewed hostnames for concurrent signed-in deployments; a token from one instance is rejected by the other's independent auth store. Filesystem access remains trusted-administrator access; this is not an adversarial multi-tenant sandbox.

## Owner sign-in and security model
The source dashboard's trusted-local model is retained:
- Host/Origin/loopback peer checks protect the service; CRM and AI controls also require per-process CSRF capabilities.
- Local board/content/CRM operator controls are **not** a general authenticated multi-user application. A same-origin local viewer is trusted to operate those local records. Do not expose this service publicly or assume the financial lock protects every tab.
- Private financial views/reports require owner authentication or a separately provisioned read-only reporting capability. Task workflow decisions require the separate purpose-bound owner session/contract checks. No role is inferred from a configurable display label.
- Explicit first-owner setup is local and interactive. Stop the new instance first, keep `DATA_DIR` exported, then run:
```sh
python3 -B spend_owner_setup.py
```
Enter a new password in the **private terminal prompt**, never chat, command arguments, source control or brand JSON. Restart afterward so the new instance-specific CRM signing key is loaded. Existing enrollment is never overwritten by this command. The application never imports the original owner's account. No reporting-agent credential is auto-provisioned by the CLI.

Do not disable CSRF, proxy identity or origin checks to connect another device. Private HTTPS/proxy deployment, trusted hosts, exposure and real owner authentication require a separately approved review. A configured TLS hostname alone is not an exposure or authentication approval.

## Feature/dependency matrix and honest parity limits
- **Overview/Agents:** full renderers, SSE/polling, dossier and usage/model panels retained. Real activity requires explicit own-instance profile routes/socket and retained source schemas. Model/account controls need compatible native adapters, accounts, allowlists and effective readback. Defaults deliberately unavailable. No existing accounts or verified model catalogue/pricing evidence ships here.
- **Office:** local responsive canvas city, rotation, zoom, reduced motion and full-screen controls retained. Active towers require verified runtime activity; decorative traffic is independent.
- **Tasks:** actual writable local board, create/update/delete/move paths and persisted readback. Native task collaboration is distinct from board records; `/workflow` commands need explicit runtime pins, approved contracts, native authority and owner-purpose sign-in. Completion never implies owner acceptance. Unknown admissions must be inspected, never blindly replayed.
- **CRM:** full companies, contacts, opportunities, drafts, notes/actions, versioned mutations, idempotency and role-scoped modules retained. Active / Qualified shows only evidence-gated retained audit decisions; Review needed holds absent/incomplete/discovery decisions and Disqualified / Rejected preserves history. Old stage labels alone never establish retention. The existing state API supplies append-only `crm-reevaluation-v1` notes and exact MIRA/SURGE create-audit provenance; own audit task IDs must be nonempty, never inherited from an original installation. Canonical score components are capacity25/need25/access20/value20/urgency10, totaling a retained80–100 with mandatory gates, reviewed opportunity, source and legitimate contact evidence. Semantic source verification remains an operator responsibility. Agent-work document references use own-instance `agent` plus relative `filename`, not host filesystem paths. Unsupported data stays pending rather than being fabricated. Browser local operations still require CSRF; configured native review authority is separate. No prospects, financial baseline or accepted drafts ship. Draft approval is not sending or collected cash.
- **Content/Docs:** local Markdown list/read/save/delete, rendering and neutral technical guide retained. No original business manual/documents ship. PDF export requires explicitly configured Chromium (`CHROMIUM_PATH`) and compatible renderer; unavailable without it. PDF export remains unvalidated in this snapshot.
- **Chat:** UI/history/recovery/attachment and send modules retained. Actual history requires explicit own group/thread/session routing; sends additionally require `CHAT_SEND_ENABLED=1` and compatible native transport/authority. No send/contact was exercised or authorized by cloning. Attachments are per-instance; uncertain work retains them, confirmed completion governs retention. No private history or uploaded files ship.
- **Schedule:** full UI/cron action/edit modules retained. Explicit own runtime and compatible installed CLI/schema required; fresh state has no November or other automatic jobs. No scheduler run/edit/resume or model/resource change was exercised against a real runtime.
- **Finance/auth:** independent empty ledger and new owner setup/auth gates retained. Missing usage/billing remains unavailable/incomplete; subscription usage is not invented additional API spending. No budget, subscription charge or actual cash collection is fabricated. Read-only reporting capability is optional and separately provisioned by the administrator.
- **Uploads:** receiver retained for owner-authorized future use, isolated under data/uploads, never auto-deploys or executes an upload. No upload was performed for this task.

Native integration enablement is **not** part of clone installation: start with `EXTERNAL_RUNTIME_ENABLED=0`. Before any approved activation, configure explicit own `HERMES_HOME`, `HERMES_PYTHON`, `NATIVE_ROOT`, `CONTROL_SOCKET`, `PROFILE_ROUTES`, group/threads and workflow origin. `config/profile-routes.example.json` describes the structure, not real identities. Required instance-local pins/endpoint/account evidence must be established against the installed runtime; no live pins or credentials have been copied. No core patches, profile creation, worker launch, service restart or live compatibility claim is included.

## Verify
```sh
python3 -B -m unittest discover -s tests -v
```
The Python suite starts **two actual fresh isolated copies** using the initializer/server commands above, on separate free loopback ports/data roots. It checks empty/disconnected state, branding, board/CRM/content persistence and isolation, version/idempotency, origin/CSRF/private-path rejection, disabled integration gates and independent owner login/cookie/lock. Test credentials are generated in-memory only and never printed; temporary clones and processes are cleaned up.

Optional frontend security unit checks (Node already installed; no install is needed or performed):
```sh
node --test tests/brand.test.cjs tests/crm_audit.test.cjs
```
The Python/Node suites are not physical-device or Windows acceptance. Prior template browser evidence is separate engineering evidence, not bundled production screenshots. This publication preparation repeats source/fresh-clone backend and security checks; it does not claim new full browser-layout verification. Physical iPhone/full-screen/orientation acceptance, optional Chromium export, native Windows application operation and real native runtime/API compatibility remain explicitly unverified.

## Packaging and publication
`SHA256SUMS` lists every shipped application file under `dashboard/` except itself. The separately supplied committed-tree manifest also covers the unchanged root LICENSE, while the external artifact manifest records bundle/source ZIP hashes. Verify the canonical source before adding your own branding or data; a custom brand file is not part of the shipped checksum list. No data, private keys, auth sessions, source backups, native pins, attachments, business records or jobs belong in the archive. `.gitignore` is defence-in-depth, not a sanitization guarantee: independently review the exact committed tree before publication.

The target destination and source-preparation scope are owner-authorised. The existing root LICENSE/history are preserved without a new licence declaration. MIRA coordinates the separate publication execution-path approval and independently verifies any eventual public commit. This preparation performs no authentication, credential access/export, push, publisher launcher, remote creation, PR, CI trigger, deployment or live dashboard change.
