# Source this from a workflow step (needs DEPLOY_KEY and RUNNER_TEMP; GH_TOKEN is sent to the GitHub API when set).
# Writes the write deploy key and GitHub's published SSH host keys to RUNNER_TEMP, exports GIT_SSH_COMMAND,
# and removes both files when the step's shell exits, even on failure.
# The deploy key is the only actor that may push directly to main under the protect-main ruleset.
key="$RUNNER_TEMP/deploy_key"
hosts="$RUNNER_TEMP/known_hosts"
trap 'rm -f "$key" "$hosts"' EXIT
(umask 077 && printf '%s\n' "$DEPLOY_KEY" > "$key")
# Unauthenticated requests share a rate limit per runner IP and can be rejected with 403; the token has its own.
curl -fsSL ${GH_TOKEN:+-H "Authorization: Bearer $GH_TOKEN"} https://api.github.com/meta | jq -r '.ssh_keys[] | "github.com \(.)"' > "$hosts"
export GIT_SSH_COMMAND="ssh -i $key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$hosts"
