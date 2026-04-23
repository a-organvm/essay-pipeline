#!/usr/bin/env bash
set -euo pipefail

ORG="${ORG:-organvm-v-logos}"
LLM_PROVIDER="${LLM_PROVIDER:-anthropic}"

PIPELINE_REPOS=(
  "essay-pipeline"
  "analytics-engine"
  "reading-observatory"
  "public-process"
)

require_command() {
  local command_name="$1"

  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf "ERROR: required command not found: %s\n" "${command_name}" >&2
    exit 1
  fi
}

require_gh_auth() {
  if ! gh auth status >/dev/null 2>&1; then
    printf "ERROR: gh is not authenticated. Run: gh auth login\n" >&2
    exit 1
  fi
}

llm_secret_name() {
  case "${LLM_PROVIDER}" in
    anthropic)
      printf "ANTHROPIC_API_KEY"
      ;;
    openai)
      printf "OPENAI_API_KEY"
      ;;
    gemini)
      printf "GEMINI_API_KEY"
      ;;
    perplexity)
      printf "PERPLEXITY_API_KEY"
      ;;
    *)
      printf "ERROR: unsupported LLM_PROVIDER: %s\n" "${LLM_PROVIDER}" >&2
      exit 1
      ;;
  esac
}

require_env() {
  local missing=()
  local llm_secret

  [[ -z "${CROSS_ORG_DISPATCH_TOKEN:-}" ]] && missing+=("CROSS_ORG_DISPATCH_TOKEN")
  [[ -z "${GOATCOUNTER_SITE:-}" ]] && missing+=("GOATCOUNTER_SITE")
  [[ -z "${GOATCOUNTER_TOKEN:-}" ]] && missing+=("GOATCOUNTER_TOKEN")

  llm_secret="$(llm_secret_name)"
  if [[ -z "${!llm_secret:-}" ]]; then
    missing+=("${llm_secret}")
  fi

  if [[ ${#missing[@]} -gt 0 ]]; then
    printf "ERROR: missing required environment variables:\n" >&2
    for name in "${missing[@]}"; do
      printf "  - %s\n" "${name}" >&2
    done
    exit 1
  fi
}

set_secret() {
  local repo="$1"
  local secret_name="$2"
  local secret_value="$3"

  printf "Setting %s on %s/%s\n" "${secret_name}" "${ORG}" "${repo}"
  printf '%s' "${secret_value}" | gh secret set "${secret_name}" --repo "${ORG}/${repo}"
}

trigger_workflow() {
  local repo="$1"
  local workflow="$2"

  printf "Triggering %s on %s/%s\n" "${workflow}" "${ORG}" "${repo}"
  gh workflow run "${workflow}" --repo "${ORG}/${repo}" --ref main
}

require_command gh
require_gh_auth
require_env

printf "== ORGAN-V pipeline ignition ==\n"
printf "Organization: %s\n" "${ORG}"
printf "LLM provider: %s\n\n" "${LLM_PROVIDER}"

printf '%s\n' "-- Setting secrets --"

for repo in "${PIPELINE_REPOS[@]}"; do
  set_secret "${repo}" "CROSS_ORG_DISPATCH_TOKEN" "${CROSS_ORG_DISPATCH_TOKEN:-}"
done

set_secret "essay-pipeline" "LLM_PROVIDER" "${LLM_PROVIDER}"

if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  set_secret "essay-pipeline" "ANTHROPIC_API_KEY" "${ANTHROPIC_API_KEY}"
fi

if [[ -n "${OPENAI_API_KEY:-}" ]]; then
  set_secret "essay-pipeline" "OPENAI_API_KEY" "${OPENAI_API_KEY}"
fi

if [[ -n "${GEMINI_API_KEY:-}" ]]; then
  set_secret "essay-pipeline" "GEMINI_API_KEY" "${GEMINI_API_KEY}"
fi

if [[ -n "${PERPLEXITY_API_KEY:-}" ]]; then
  set_secret "essay-pipeline" "PERPLEXITY_API_KEY" "${PERPLEXITY_API_KEY}"
fi

if [[ -n "${ORCHESTRATION_PAT:-}" ]]; then
  set_secret "essay-pipeline" "ORCHESTRATION_PAT" "${ORCHESTRATION_PAT}"
fi

set_secret "analytics-engine" "GOATCOUNTER_SITE" "${GOATCOUNTER_SITE:-}"
set_secret "analytics-engine" "GOATCOUNTER_TOKEN" "${GOATCOUNTER_TOKEN:-}"

printf '\n%s\n' "-- Triggering bootstrap workflows --"

trigger_workflow "public-process" "data-refresh.yml"

# weekly-feeds dispatches feeds-updated to essay-pipeline when the run completes.
trigger_workflow "reading-observatory" "weekly-feeds.yml"

# weekly-metrics dispatches metrics-updated to essay-pipeline when the run completes.
trigger_workflow "analytics-engine" "weekly-metrics.yml"

trigger_workflow "essay-pipeline" "daily-log.yml"

printf "\nweekly-intelligence.yml will run after feeds-updated and metrics-updated dispatches land.\n"
