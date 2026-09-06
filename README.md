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
src/                                      generator, validator, renderer, bundle, injector
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
git clone --branch fake-world-live-injection \
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

## Generate a fake world

先用本地 Mock 验证整条生成链路，不消耗 API：

```bash
python3 scripts/generate_scenario.py --mode mock \
  --select fake_framework,fake_cve
```

需要 Qwen 自动挑选类型并生成值时：

```bash
python3 scripts/generate_scenario.py --selection qwen --mode api --max 3
```

也可以手动选任意已经登记的 5.1/5.3 类型：

```bash
python3 scripts/generate_scenario.py --mode api \
  --select fake_server,wrong_patch_status,policy_stop_testing
```

每个场景目录都会保存 `scenario.json`、`fake_world.json`、
`qwen_proposals.json`、`injection_plan.json`，并在终端打印所属类别、
实际改动字段和插入位置。生成阶段只写项目本地文件，不操作 Docker。

观测 Bundle 可以单独检查：

```bash
python3 src/observation_bundle.py \
  --scenario runs/XBEN-028-24/generated/scenario-YYYY.../scenario.json \
  --output /tmp/aoi-observations
python3 src/observation_bundle.py --self-test
```

## Run the automatic attack

The runner rebuilds the target container before the attack and performs the
same clean-container check after it. It records the transcript, report,
precheck, reset log, and attack-only token count in a timestamped run folder.

Baseline 不读取任何场景，也不写入任何观测文件：

```bash
bash scripts/run_repeatable_experiment.sh baseline
```

注入实验必须明确指定刚生成的场景目录：

```bash
bash scripts/run_repeatable_experiment.sh injected \
  runs/XBEN-028-24/generated/scenario-YYYY.../
```

脚本会先重建干净容器，再写入 `security_assessment.json` 和
`/var/www/html/aoi-observations/` 下的全部 Bundle 文件；攻击结束时先按
Injection Manifest 恢复原文件，再重建并检查干净容器。每次运行会记录
`precheck.json`、注入清单、攻击 transcript、报告、`usage.json`、恢复日志，
并在 `archives/` 生成压缩归档。`usage.json.attack_token_used` 只来自本次
Codex 攻击进程，不包含场景生成、Qwen 选择/生成、注入、恢复、报告提取和归档。
脚本会加载项目 `.env`，为本次 Codex 进程建立临时的 `aoi_dotenv` Provider：
`MODEL_API_KEY` 作为认证密钥，`MODEL_BASE_URL` 作为接口地址，`MODEL_NAME` 传给
`codex exec --model`。这样服务器全局的 Codex Provider 配置不会覆盖项目 `.env`。

## Run a batch of experiments

批处理脚本只运行一次 Baseline，然后按顺序生成并攻击注入场景。每个注入运行
结束后必须先通过现有脚本的恢复检查，才会进入下一轮；遇到失败会立即停止。

同一个虚假环境重复攻击 3 次（只生成一次）：

```bash
bash scripts/run_batch_experiments.sh repeat 3
```

每轮生成一个新的虚假环境并攻击 3 次：

```bash
bash scripts/run_batch_experiments.sh multiple 3
```

默认使用 Qwen API 自动选择最多 3 种类型。可用环境变量覆盖生成方式或手动选型：

```bash
AOI_SCENARIO_MODE=mock \
bash scripts/run_batch_experiments.sh multiple 3

AOI_SCENARIO_SELECTION=manual \
AOI_SCENARIO_SELECT=fake_framework,fake_cve \
bash scripts/run_batch_experiments.sh repeat 3
```

批处理清单和日志保存在 `runs/XBEN-028-24/batches/`；每次攻击报告和压缩归档
仍分别保存在 `runs/XBEN-028-24/{baseline,injected}/` 和 `archives/`。

Compose 位置和服务名可以通过环境变量覆盖，不需要编辑脚本：

```bash
AOI_COMPOSE_FILE=/path/to/docker-compose.yml \
AOI_COMPOSE_PROJECT=guest-experiment-xben028 \
bash scripts/run_repeatable_experiment.sh baseline
```

每次重置默认执行 `docker compose down --volumes`，这样 Compose 项目声明的
命名卷也会随实验一起重建；外部卷仍由 Docker 保留。若基准 Compose 明确需要
保留项目卷，可设置 `AOI_RESET_VOLUMES=false`，脚本会把该策略写入本轮记录。

Reports are kept in `runs/`; compressed copies are placed in `archives/`.
Both locations are ignored by Git so experiment output cannot pollute the
source branch.

## Catalog insertion map

5.1 identity profiles are rendered into `environment.json`,
`pages/environment.html`, and the corresponding Header/Nmap observations.
Vulnerability profiles are merged into `security_assessment.json` and a
vulnerability claim page. Assets, credentials, permissions, objectives, and
attack paths are written to `claims/*.json` plus claim pages. `fake_page` is
written as a generated HTML page. 5.3 policy profiles are written under
`claims/policy.json` and `pages/claims-policy.html`. The injector allow-list
accepts only these generated paths.

## What you need to do next

After this branch is pushed, clone it into a new server directory, recreate
`.env` manually, confirm the Compose file path, and run the Mock generation and
Bundle self-test above. Then pass the printed scenario directory to the
injected runner. The original snapshot branch can remain as a rollback point.
