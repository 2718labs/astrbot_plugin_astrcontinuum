# Configuration reference

English | [简体中文](CONFIGURATION.zh-CN.md)

This page is the public reference for `_conf_schema.json`. It describes configuration boundaries;
it is not evidence that every optional integration is enabled on a particular AstrBot host.
Never commit keys, key-file locations, Provider credentials, local databases, or host-specific
configuration.

## Safe defaults

| Setting | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | `bool` | `true` | Enables AstrContinuum's optional request enhancement. |
| `context_engine_mode` | `string` | `active` | `active` uses a graph result only after full validation; `shadow` measures without replacing the deterministic result; `off` uses the deterministic path only. |
| `model_context_limit` | `int` | `0` | `0` asks AstrBot's public Provider metadata for the current window; missing or incompatible metadata falls back conservatively to `128000`. A positive value is a manual limit. |
| `target_input_budget` | `int` | `130000` | Desired maximum for the assembled input. |
| `hard_input_ceiling` | `int` | `150000` | Safety ceiling for the controlled Provider View. |
| `compaction_start_ratio` | `float` | `0.75` | Ratio at which durable compaction intent may be raised. |
| `provider_view_switch_ratio` | `float` | `0.80` | Ratio at which the temporary controlled Provider View may be selected. |

The online hook never waits for a compiler, semantic auditor, background worker, or remote model.
Changing a budget or ratio does not authorize a partial publication or bypass a durable invariant.

## Key management

| Setting | Type | Default | Boundary |
| --- | --- | --- | --- |
| `encryption_key_source` | `string` | `local` | `local`, `file`, or `environment`. `local` creates and reuses a server-side key in AstrBot's plugin data directory. |
| `encryption_key_file` | `string` | empty | Shown only when `encryption_key_source=file`; it is an external key-file path, never key material. |
| `encryption_previous_key_file` | `string` | empty | Shown only in `file` mode during a controlled key rotation. |

For `environment`, use external secret management to provide the documented environment variables;
do not enter a secret in the WebUI, chat, log, command-line argument, or repository file. During a
file-based rotation, stop or reload the host according to the operator procedure, verify
`/context_status` reports an active new key and completed maintenance, then remove the previous
key reference. See [Security](../SECURITY.md) for disclosure guidance.

## Provider and compatibility settings

| Setting | Type | Default | Boundary |
| --- | --- | --- | --- |
| `compaction_provider_id` | `string` | empty | An explicit compaction provider when it resolves. Empty follows the current conversation provider. If neither an explicit provider nor public current-provider capability is available, the provider-bound worker lane stays absent and the host request fails open. |
| `thinking_compat_openai_provider_ids` | `list` | `[]` | Advanced allowlist for confirmed OpenAI Chat Completions-compatible Provider IDs when the active model requires a temporary compatibility copy. It never rewrites durable host history. |

Setting a Provider does not enable reorganization: the ordinary Provider-bound/default AstrBot
path leaves `reorganization_token_budget` unset and publishes an empty ledger. Only an injected
compiler backend may set an explicit budget for fenced synthetic Gate A validation; that internal
test configuration is not a public AstrBot Provider setting or a production capability claim.

## Operator checks

After changing configuration:

1. Restart or reload the plugin through AstrBot.
2. Use `/context_status` to inspect content-free health, key, model-window, and worker state.
3. Use `/context_inspect` for the current session's content-free evidence only.
4. For a release decision, run the complete test and AstrBot probe gates in the
   [test matrix](TEST_MATRIX.md); a configuration screen is not a compatibility receipt.

Related pages: [AstrBot integration](ASTRBOT_INTEGRATION.md),
[Architecture](ARCHITECTURE.md), and [Security](../SECURITY.md).
