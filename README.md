# AOI

AOI is a repeatable fake-environment experiment project for XBEN-028-24. It
keeps generation, validation, observation rendering, injection selection, and
the fully automatic Codex attack workflow in one directory.

## Directory layout

```text
configs/
  real_world.json                         clean source environment
  fake_world.json                         latest local generated world
  manipulation_catalog.json               5.1/5.3 manipulation catalog
  fixtures/api_vulnerabilities.baseline.json
                                           versioned vulnerability fixture
prompts/                                  generator prompts
src/                                      generator, validator, renderer, pipeline
scripts/generate_scenario.py               Qwen selection and scenario builder
scripts/run_repeatable_experiment.sh      baseline/injected attack runner
runs/                                     local experiment output (ignored)
archives/                                 local compressed reports (ignored)
```

The old `runs/`, `outputs/`, `checkpoint_*`, `workspace/`, and backup files
were historical server artifacts. They are intentionally excluded from this
clean development branch. The complete server snapshot remains available in
Git as `server-full-snapshot-20260905_140645`.

## First setup on the server

```bash
git clone --branch codex-clean-reorganized \
  https://github.com/6sixsixsixsix6/AOI.git \
  /home/guest-experiment/aoi-project-clean
cd /home/guest-experiment/aoi-project-clean
cp .env.example .env
${EDITOR:-vi} .env
```

Fill `.env` manually. The expected values are:

```dotenv
MODEL_API_KEY=YOUR_QWEN_API_KEY
MODEL_BASE_URL=https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
MODEL_NAME=qwen3.8-max
```

`.env` is ignored and is never included in commits or archives.

## Generate and validate a fake world

Run a local no-cost mock first:

```bash
python3 src/pipeline.py --mode mock \
  --manipulation fake_version --target web_server.version
python3 src/validator.py
```

Use Qwen for a generated value after the mock passes:

```bash
python3 src/pipeline.py --mode api \
  --manipulation fake_version --target web_server.version
```

The catalog-driven scenario builder can let Qwen choose up to three enabled
5.1/5.3 profiles and records the selection and injection plan:

```bash
python3 scripts/generate_scenario.py --selection qwen --max 3
```

Use `--select fake_framework,fake_cve` for an explicit selection. Every
scenario is written under `runs/XBEN-028-24/generated/`. This command creates
the scenario artifacts; it does not apply arbitrary catalog entries to the
Docker target yet.

## Run the automatic attack

The runner rebuilds the target container before the attack and performs the
same clean-container check after it. It records the transcript, report,
precheck, reset log, and attack-only token count in a timestamped run folder.

```bash
bash scripts/run_repeatable_experiment.sh baseline
bash scripts/run_repeatable_experiment.sh injected
```

The injected command uses
`configs/fixtures/api_vulnerabilities.baseline.json` by default. A different
fixture can be supplied as the second argument. The Compose location can be
overridden without editing the script:

```bash
AOI_COMPOSE_FILE=/path/to/docker-compose.yml \
AOI_COMPOSE_PROJECT=guest-experiment-xben028 \
bash scripts/run_repeatable_experiment.sh baseline
```

The live injected runner currently applies the `wrong_patch_status` profile to
one of the two fixture vulnerabilities. The remaining catalog profiles are
kept in the generation/plan layer for the next injection implementation.

Reports are kept in `runs/`; compressed copies are placed in `archives/`.
Both locations are ignored by Git so experiment output cannot pollute the
source branch.

## What you need to do next

After this branch is pushed, clone it into a new server directory, recreate
`.env` manually, confirm the Compose file path, and run the mock validation
command above. Do not delete the existing snapshot branch until the clean
clone has passed that check.
