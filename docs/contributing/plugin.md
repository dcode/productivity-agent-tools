# Writing a product plugin

A *product* is one service the server can talk to. Every product, including
the built-in ones, is found through a Python entry point, so a new one can live
in its own package: install it beside the server and add its table to the
config, and its tools appear. Nothing in this repository has to change.

This page builds one from scratch: **public holidays**, from the free
[Nager.Date](https://date.nager.at) API, with two tools an agent can use when
planning across days. The complete, tested package is in
[`examples/holidays-plugin`](https://github.com/dcode/productivity-agent-tools/tree/main/examples/holidays-plugin);
every listing below is read from those files, so what you see here is what the
test suite runs.

## Remote or local

There are two kinds of product:

- A **`RemoteProduct`** is served by an API. It has a connection (`base_url`,
  TLS, cookies, timeout), a credential and an adapter. Jira, Bitbucket,
  Confluence, ServiceNow and Outlook are remote, and so is this example.
- A **`LocalProduct`** works on local data, such as the Obsidian vault. Instead
  of an adapter and a credential it has an `open` that builds its client from
  its settings. Everything else on this page applies to it too.

## The pieces

```text
examples/holidays-plugin/
├── pyproject.toml          the entry point that makes it a plugin
└── src/pat_holidays/
    ├── settings.py         the [holidays] config table
    ├── client.py           the adapter (HTTP, errors) and the client (what to ask)
    ├── tools.py            the MCP tools
    └── product.py          the product object, its credential and its checks
```

### The entry point

The server finds products under the `productivity_agent_tools.products`
entry-point group. The name on the left is the product's name: its config table
and the prefix of its tools. It must equal the product object's `name`.

```toml
--8<-- "examples/holidays-plugin/pyproject.toml:entry-point"
```

### Settings

A product's settings class extends `ProductSettings`, which already has the
connection keys every product shares (`base_url`, `ca_bundle`, `cookies`,
`timeout_seconds`, `dry_run`, `group` and the token sources).
`product_overrides` reads those; the parser reads the product's own.

```python
--8<-- "examples/holidays-plugin/src/pat_holidays/settings.py"
```

Giving `base_url` a default matters for a public API: without one, the product
would fall back to the top-level `base_url`, which in most configs points at
some other service.

### The adapter and the client

The **adapter** is the only code that sees HTTP. Subclass `HttpAdapter`, which
already handles TLS trust, cookies, timeouts, logging, connectivity errors and
dry-run, and implement `_interpret`: turn a response into data, or into an
`AgentToolsError` whose message tells the agent (and the user) what to do.

The **client** is what the tools call. It knows the API's paths and gets an
`ApiClient`, never an HTTP library, which is what makes it testable and keeps
secrets out of it.

```python
--8<-- "examples/holidays-plugin/src/pat_holidays/client.py"
```

### The tools

Tools are where the [architecture](../architecture.md) pays off or doesn't. A
good tool:

- **Answers one question.** `holidays_next` rather than `holidays_get` with a
  mode switch.
- **Returns a typed model with only what matters.** Here, one line per holiday
  instead of Nager.Date's nine fields: the agent reads a few dozen bytes per
  holiday, not a few hundred.
- **Has a docstring that is its manual.** The agent sees the first paragraph,
  each parameter's `Args` text and the result's field descriptions, and nothing
  else.
- **Bounds its output.** Limits as `Annotated[int, Field(...)]`, defaults small.
- **Is marked read or write** (`READ` here), and wrapped in `@anticipated`, so an
  `AgentToolsError`'s message reaches the agent intact.

```python
--8<-- "examples/holidays-plugin/src/pat_holidays/tools.py"
```

Tool names start with the product's name and an underscore. That keeps them
apart from other products' and lets an agent's approval rules match a product.

A product with more than a handful of tools can group them into toolsets, so a
config can offer only some (see [Toolsets](../configuration.md#toolsets)).
Untagged tools are in `core`. Tag the others with
`@server.tool(annotations=READ, meta=toolset("history"))`, using `toolset` from
`productivity_agent_tools.toolsets`, and declare each extra toolset with a
sentence on the product: `toolsets={"history": "Holidays in past years."}`. A
tool tagged with a toolset its product does not declare fails when the server
starts.

### The product

The product object ties it together. `instructions` is added to the server's
instructions while the product is on: say when to use the tools, and which
ones change shared data. `doctor` yields a line per check that passed and
raises on the first that fails; `productivity-agent-tools doctor` runs it.

```python
--8<-- "examples/holidays-plugin/src/pat_holidays/product.py"
```

A public API needs no credential, so `no_credential` returns an empty set of
headers. A product with a token leaves `resolve_credential` out: the default
reads a personal access token from the environment, `token_cmd`, an env file or
the keychain, under the product's name, and `set-token holidays` would store
one.

## Tools that change things

The example only reads. A write tool follows three rules, all visible in the
built-in products (`jira/tools.py` is a good model):

1. **Send it through `ApiClient.write`.** While `dry_run` is on, it sends
   nothing and returns the request it would have sent, which the agent shows
   the user. Never write with `get` or `query`.
2. **Report the outcome with `writes.report`** into a result model that extends
   `WriteReport`, and **wrap the send in `writes.once`** with a key that
   identifies the change, so an identical call within 30 seconds is not sent
   twice.
3. **Mark it `WRITE`** (or `IDEMPOTENT_WRITE` when repeating it changes
   nothing), say in its docstring that it changes shared data and to confirm
   with the user first, and name the read that should come before it.

## Testing

Test against a fake of the service, never the real one. `httpx.MockTransport`
answers requests from a function, and `build_server` and `parse_settings` take
the products to use, so the plugin is tested without being installed:

```python
--8<-- "tests/test_example_plugin.py:fake"
```

The full test, including the doctor, the errors and the settings, is
[`tests/test_example_plugin.py`](https://github.com/dcode/productivity-agent-tools/blob/main/tests/test_example_plugin.py).

## Installing and trying it

Install the plugin into the same environment as the server (the server is not
on PyPI, so the plugin's dependency on it is met by the installed copy):

```bash
uv pip install -e examples/holidays-plugin
```

Add its table to the config:

```toml
[holidays]
country = "DE"
```

Then check it and call it without an agent:

```bash
uv run productivity-agent-tools doctor
uv run productivity-agent-tools call holidays_next max_results=3
```

Restart your agent and the `holidays_` tools are offered. A product of your own
can live in its own repository the same way; contributing it here instead
means a sub-package under `src/productivity_agent_tools/`, its entry point in
this repository's `pyproject.toml`, tests in `tests/`, a page under
`docs/products/` and a line in the roadmap.

## When a product needs another

Products never import each other. Two patterns cover working together:

- **An integration** adds tools that need two products at once, registered only
  when both are on (`links.py`: a Jira issue's Bitbucket pull requests). It is
  found through the `productivity_agent_tools.integrations` entry-point group.
- **A capability** lets one product use another's data without needing it:
  `ToolContext.peers_of(SomeProtocol)` returns the clients of the other products
  that implement a protocol from `capabilities.py` (the Obsidian sync gathers
  tasks this way from whichever products offer them).
