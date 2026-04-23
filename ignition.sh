#!/usr/bin/env bash
set -euo pipefail

# ORGAN-V Essay Pipeline — Ignition Script
# Pushes repository secrets from environment variables and triggers bootstrap workflows.
# Usage: ORG=a-organvm LLM_PROVIDER=anthropic ./ignition.sh [--dry-run] [--skip-triggers]

readonly SCRIPT_VERSION="1.1.0"

# -- Configuration ---------------------------------------------------------------

ORG="${ORG:-a-organvm}"
LLM_PROVIDER="${LLM_PROVIDER:-anthropic}"
DRY_RUN=false
SKIP_TRIGGERS=false

# Repos that participate in the ORGAN-V essay pipeline.
# Confirmed via sibling workflow inspection (2026-04-23):
#   essay-pipeline      — essay-generation.yml, daily-log.yml, weekly-intelligence.yml, ci.yml
#   analytics-engine    — weekly-metrics.yml (dispatches metrics-updated)
#   reading-observatory — weekly-feeds.yml (dispatches feeds-updated)
#   public-process      — data-refresh.yml, pages.yml, auto-merge.yml
#   editorial-standards — ci.yml (checked out by essay-pipeline workflows for schemas)
PIPELINE_REPOS=(
  "essay-pipeline"
  "analytics-engine"
  "reading-observatory"
  "public-process"
  "editorial-standards"
)

# Secrets shared across all pipeline repos (cross-org dispatch token).
SHARED_SECRETS=(
  "CROSS_ORG_DISPATCH_TOKEN"
)

# Provider-specific LLM credential names.
declare -A LLM_CREDENTIALS=(
  [anthropic]="ANTHROPIC_API_KEY"
  [openai]="OPENAI_API_KEY"
  [gemini]="GEMINI_API_KEY"
  [perplexity]="PERPLEXITY_API_KEY"
)

# -- Argument parsing ------------------------------------------------------------

for arg in "$@"; do
  case "${arg}" in
    --dry-run)  DRY_RUN=true ;;
    --skip-triggers) SKIP_TRIGGERS=true ;;
    --help|-h)
      printf "Usage: %s [--dry-run] [--skip-triggers]\n\n" "$0"
      printf "Environment variables:\n"
      printf "  ORG                      GitHub org (default: a-organvm)\n"
      printf "  LLM_PROVIDER             LLM provider: anthropic|openai|gemini|perplexity (default: anthropic)\n"
      printf "  CROSS_ORG_DISPATCH_TOKEN PAT for cross-repo workflow dispatch (required)\n"
      printf "  ANTHROPIC_API_KEY        Anthropic API key (required when LLM_PROVIDER=anthropic)\n"
      printf "  OPENAI_API_KEY           OpenAI API key (required when LLM_PROVIDER=openai)\n"
      printf "  GEMINI_API_KEY           Gemini API key (required when LLM_PROVIDER=gemini)\n"
      printf "  PERPLEXITY_API_KEY       Perplexity API key (required when LLM_PROVIDER=perplexity)\n"
      printf "  GOATCOUNTER_SITE         GoatCounter site ID (required)\n"
      printf "  GOATCOUNTER_TOKEN        GoatCounter API token (required)\n"
      printf "  ORCHESTRATION_PAT        Optional orchestration PAT\n"
      exit 0
      ;;
    *)
      printf "ERROR: unknown argument: %s\n" "${arg}" >&2
      printf "Run %s --help for usage.\n" "$0" >&2
      exit 1
      ;;
  esac
done

# -- Preflight: commands ---------------------------------------------------------

preflight_commands() {
  local commands=("gh" "git")
  local failed=false

  printf "Checking required commands...\n"
  for cmd in "${commands[@]}"; do
    if command -v "${cmd}" >/dev/null 2>&1; then
      printf "  [ok] %s (%s)\n" "${cmd}" "$(command -v "${cmd}")"
    else
      printf "  [FAIL] %s — not found in PATH\n" "${cmd}" >&2
      failed=true
    fi
  done

  if [[ "${failed}" == "true" ]]; then
    printf "ERROR: missing required commands. Install them and retry.\n" >&2
    exit 1
  fi
}

# -- Preflight: gh authentication ------------------------------------------------

preflight_gh_auth() {
  printf "\nChecking gh authentication...\n"

  if ! gh auth status >/dev/null 2>&1; then
    printf "  [FAIL] gh is not authenticated.\n" >&2
    printf "  Run: gh auth login\n" >&2
    exit 1
  fi

  # Extract and display the authenticated user.
  local gh_user
  gh_user="$(gh api user --jq '.login' 2>/dev/null || printf 'unknown')"
  printf "  [ok] Authenticated as: %s\n" "${gh_user}"

  # Verify the org is accessible.
  printf "  Verifying org access: %s\n" "${ORG}"
  if ! gh api "orgs/${ORG}" --jq '.login' >/dev/null 2>&1; then
    printf "  [FAIL] Cannot access org '%s'. Check permissions or org name.\n" "${ORG}" >&2
    exit 1
  fi
  printf "  [ok] Org '%s' is accessible.\n" "${ORG}"

  # Verify each pipeline repo is accessible.
  local repo_failed=false
  for repo in "${PIPELINE_REPOS[@]}"; do
    if gh api "repos/${ORG}/${repo}" --jq '.full_name' >/dev/null 2>&1; then
      printf "  [ok] Repo: %s/%s\n" "${ORG}" "${repo}"
    else
      printf "  [FAIL] Repo not found or no access: %s/%s\n" "${ORG}" "${repo}" >&2
      repo_failed=true
    fi
  done

  if [[ "${repo_failed}" == "true" ]]; then
    printf "ERROR: one or more pipeline repos are inaccessible.\n" >&2
    exit 1
  fi
}

# -- Preflight: environment variables --------------------------------------------

preflight_env() {
  local missing=()
  local present=()

  printf "\nChecking environment variables...\n"

  # Required shared secrets.
  for secret_name in "${SHARED_SECRETS[@]}"; do
    if [[ -n "${!secret_name:-}" ]]; then
      present+=("${secret_name}")
    else
      missing+=("${secret_name}")
    fi
  done

  # GoatCounter credentials (required for analytics-engine).
  for var in GOATCOUNTER_SITE GOATCOUNTER_TOKEN; do
    if [[ -n "${!var:-}" ]]; then
      present+=("${var}")
    else
      missing+=("${var}")
    fi
  done

  # Provider-specific LLM credential.
  if [[ -z "${LLM_CREDENTIALS[${LLM_PROVIDER}]+_}" ]]; then
    printf "  [FAIL] Unsupported LLM_PROVIDER: %s\n" "${LLM_PROVIDER}" >&2
    printf "  Supported providers: %s\n" "$(printf '%s ' "${!LLM_CREDENTIALS[@]}")" >&2
    exit 1
  fi

  local llm_secret="${LLM_CREDENTIALS[${LLM_PROVIDER}]}"
  if [[ -n "${!llm_secret:-}" ]]; then
    present+=("${llm_secret}")
  else
    missing+=("${llm_secret}")
  fi

  # Report present vars.
  for name in "${present[@]}"; do
    printf "  [ok] %s\n" "${name}"
  done

  # Report optional vars.
  if [[ -n "${ORCHESTRATION_PAT:-}" ]]; then
    printf "  [ok] ORCHESTRATION_PAT (optional)\n"
  else
    printf "  [--] ORCHESTRATION_PAT (optional, skipped)\n"
  fi

  # Report any additional LLM keys available beyond the primary provider.
  for provider in "${!LLM_CREDENTIALS[@]}"; do
    local key="${LLM_CREDENTIALS[${provider}]}"
    # Skip the primary provider (already reported above).
    if [[ "${provider}" == "${LLM_PROVIDER}" ]]; then
      continue
    fi
    if [[ -n "${!key:-}" ]]; then
      printf "  [ok] %s (extra provider: %s)\n" "${key}" "${provider}"
    fi
  done

  # Fail on missing required vars.
  if [[ ${#missing[@]} -gt 0 ]]; then
    printf "\nERROR: missing required environment variables:\n" >&2
    for name in "${missing[@]}"; do
      printf "  - %s\n" "${name}" >&2
    done
    exit 1
  fi
}

# -- Secret push -----------------------------------------------------------------

set_secret() {
  local repo="$1"
  local secret_name="$2"
  local secret_value="$3"

  if [[ "${DRY_RUN}" == "true" ]]; then
    printf "  [dry-run] Would set %s on %s/%s\n" "${secret_name}" "${ORG}" "${repo}"
    return
  fi

  printf "  Setting %s on %s/%s\n" "${secret_name}" "${ORG}" "${repo}"
  printf '%s' "${secret_value}" | gh secret set "${secret_name}" --repo "${ORG}/${repo}"
}

# -- Workflow trigger ------------------------------------------------------------

trigger_workflow() {
  local repo="$1"
  local workflow="$2"

  if [[ "${DRY_RUN}" == "true" ]]; then
    printf "  [dry-run] Would trigger %s on %s/%s\n" "${workflow}" "${ORG}" "${repo}"
    return
  fi

  printf "  Triggering %s on %s/%s\n" "${workflow}" "${ORG}" "${repo}"
  gh workflow run "${workflow}" --repo "${ORG}/${repo}" --ref main
}

# -- Main ------------------------------------------------------------------------

printf "== ORGAN-V Essay Pipeline Ignition v%s ==\n\n" "${SCRIPT_VERSION}"

# Preflight gates — all three must pass before any mutations.
preflight_commands
preflight_gh_auth
preflight_env

printf "\n== Configuration ==\n"
printf "  Organization:  %s\n" "${ORG}"
printf "  LLM provider:  %s\n" "${LLM_PROVIDER}"
printf "  Dry run:       %s\n" "${DRY_RUN}"
printf "  Skip triggers: %s\n" "${SKIP_TRIGGERS}"

# -- Push secrets ----------------------------------------------------------------

printf "\n== Setting secrets ==\n"

# Shared cross-org dispatch token on all pipeline repos.
for repo in "${PIPELINE_REPOS[@]}"; do
  set_secret "${repo}" "CROSS_ORG_DISPATCH_TOKEN" "${CROSS_ORG_DISPATCH_TOKEN:-}"
done

# LLM provider selection and credentials on essay-pipeline.
set_secret "essay-pipeline" "LLM_PROVIDER" "${LLM_PROVIDER}"

# Push all available LLM API keys (the primary is required; extras are optional).
for provider in "${!LLM_CREDENTIALS[@]}"; do
  key="${LLM_CREDENTIALS[${provider}]}"
  if [[ -n "${!key:-}" ]]; then
    set_secret "essay-pipeline" "${key}" "${!key}"
  fi
done
unset key

# Optional orchestration PAT.
if [[ -n "${ORCHESTRATION_PAT:-}" ]]; then
  set_secret "essay-pipeline" "ORCHESTRATION_PAT" "${ORCHESTRATION_PAT}"
fi

# GoatCounter credentials for analytics-engine.
set_secret "analytics-engine" "GOATCOUNTER_SITE" "${GOATCOUNTER_SITE:-}"
set_secret "analytics-engine" "GOATCOUNTER_TOKEN" "${GOATCOUNTER_TOKEN:-}"

# -- Trigger bootstrap workflows -------------------------------------------------

if [[ "${SKIP_TRIGGERS}" == "true" ]]; then
  printf "\n== Skipping workflow triggers (--skip-triggers) ==\n"
else
  printf "\n== Triggering bootstrap workflows ==\n"

  trigger_workflow "public-process" "data-refresh.yml"

  # weekly-feeds dispatches feeds-updated to essay-pipeline on completion.
  trigger_workflow "reading-observatory" "weekly-feeds.yml"

  # weekly-metrics dispatches metrics-updated to essay-pipeline on completion.
  trigger_workflow "analytics-engine" "weekly-metrics.yml"

  trigger_workflow "essay-pipeline" "daily-log.yml"

  printf "\n  weekly-intelligence.yml will run after feeds-updated and metrics-updated dispatches land.\n"
fi

printf "\n== Ignition complete ==\n"
