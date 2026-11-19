# Authentication

| Product | Credential | How you supply it |
|---|---|---|
| Jira, Bitbucket, Confluence | Personal access token, sent as `Authorization: Bearer ...` | `set-token`, an environment variable, `token_cmd` or `token_env_file` |
| ServiceNow | The browser session from your sign-in | `login servicenow` |
| Outlook mail and calendar | A refresh token, renewed into short-lived access tokens | `login microsoft` |

None of them asks for or stores a password. Whatever they keep is in the system
keychain: the macOS Keychain, or the Secret Service on Linux.

## Personal access tokens

Jira, Bitbucket and Confluence each have their own token, named after the
product: `JIRA_TOKEN`, `BITBUCKET_TOKEN`, `CONFLUENCE_TOKEN`. For each, these
sources are checked in order and the first one found wins:

1. **The environment variable**, such as `JIRA_TOKEN`.
2. **`token_cmd`**: a command that prints the token.
3. **`token_env_file`**: an env file with lines such as `JIRA_TOKEN=...`.
4. **The system keychain**, where `set-token` stores it.

`token_cmd` and `token_env_file` can be set at the top of the config, in a
`[group.<name>]` table, or in a product's own table; the most specific wins,
and each is resolved separately. If no source has a token, the error lists
where it looked and every way to supply one.

### `set-token`

```bash
uv run productivity-agent-tools set-token jira
```

The token is typed at a hidden prompt and stored in the system keychain. With
no name, `set-token` lists the tokens it can store and asks which one; without
a terminal (in a script) it prints the list and stops instead of guessing. An
exported variable, a `token_cmd` or an env file entry, if you have one, is used
before the stored token.

### `token_cmd`

A command whose output is the token, such as a `sops`, `pass` or `op` call:

```toml
# One encrypted file for every product's token.
token_cmd = "sops -d --extract '[\"{product}\"]' ~/.secrets/tokens.yaml"
```

- `{product}` is replaced by the product name (`jira`, `bitbucket`,
  `confluence`), so one command can serve every product. It is a plain text
  match, not a template language, so braces a program needs (awk's
  `{ print $2 }`, jq filters) are left alone.
- **As a string**, it is read the way a shell reads a command line: `~`,
  `$HOME` and `${HOME}` expand, except inside single quotes, and quotes group
  words. So `awk -F= '{ print $2 }' ~/.secrets/t.env` works.
- **As a list** (`["pass", "show", "work/{product}"]`), it is used exactly as
  written, with no expansion, for when a `$` must reach the program.
- It runs **without a shell**, so pipes and redirections mean nothing; use
  `sh -c '...'` for those. It gets no input and has 30 seconds to finish.
- It must print the token on one line. Its output is never logged; if it fails,
  the error shows its exit status and the first line of what it printed to
  stderr.

### `token_env_file`

An env file with `<PRODUCT>_TOKEN=` lines. Blank lines, `#` comments, a leading
`export` and quotes around the value are all handled, and a value containing
`=` is kept whole.

```toml
# One file for several products...
token_env_file = "${HOME}/.secrets/tokens.env"

# ...or one file per product.
token_env_file = "~/.secrets/{product}_token.env"
```

With `{product}` in the path, a product that has no file of its own is skipped
(its token is somewhere else, such as the keychain). A fixed path that does not
exist is an error, since it is most likely a typo. `--token-env-file` on the
command line overrides the setting at every level.

## Browser sign-in

ServiceNow and Outlook sit behind single sign-on, so there is no token to paste.
`login` opens Chrome (or Edge) on a new, temporary profile, you sign in there as
you normally do, smart card included, and the window closes by itself once the
sign-in is done.

```bash
uv run productivity-agent-tools login servicenow
uv run productivity-agent-tools login microsoft
```

What `login` does and does not do:

- It never sees your smart card, PIN or password. Those stay between you and
  the browser.
- It talks to the browser over a private pipe (no listening debug port), uses
  only browser-level commands (it never attaches to the page), and deletes the
  temporary profile afterwards. Your everyday browser profile is not touched.
- What it stores is never printed or logged.
- A server that is already running picks up a new sign-in without a restart.

`login` needs macOS or Linux and a browser whose policy allows remote debugging.
Set `browser` (a path or command name) and `login_timeout_seconds` (default
300) under `[servicenow]` or `[microsoft]` if you need to.

What each sign-in collects, and how long it lasts, is on the
[ServiceNow](products/servicenow.md) and [Outlook](products/outlook.md) pages.
