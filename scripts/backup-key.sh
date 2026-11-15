# Source this from the backup step of a workflow (needs BACKUP_DEPLOY_KEY and RUNNER_TEMP; GH_TOKEN is sent to the GitHub
# API when set). Same discipline as deploy-key.sh, for the key of the PRIVATE backup repository ravidorr/misra-backups:
# the key and GitHub's published SSH host keys go to files with unique names in RUNNER_TEMP, GIT_SSH_COMMAND is exported
# so that ssh uses that one key file only (IdentitiesOnly, no agent) and checks the host key, and both files, with the
# step's work directory when BACKUP_WORK is set, are removed when the step's shell exits, even on failure.
# This key can write the backup repository and nothing else; it is never used for this repository.
key="$(mktemp "$RUNNER_TEMP/backup_key.XXXXXX")"
hosts="$(mktemp "$RUNNER_TEMP/backup_hosts.XXXXXX")"
trap 'rm -rf "$key" "$hosts" ${BACKUP_WORK:+"$BACKUP_WORK"}' EXIT
(umask 077 && printf '%s\n' "$BACKUP_DEPLOY_KEY" > "$key")
curl -fsSL ${GH_TOKEN:+-H "Authorization: Bearer $GH_TOKEN"} https://api.github.com/meta | jq -r '.ssh_keys[] | "github.com \(.)"' > "$hosts"
export GIT_SSH_COMMAND="ssh -i $key -o IdentitiesOnly=yes -o IdentityAgent=none -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$hosts"
