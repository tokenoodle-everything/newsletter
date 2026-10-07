---
title: New generation of tn-venv
date: 2026-10-07
author: aiwonderland
summary: New generation of tn-venv is here!
---

# New generation

### Added
- `tn-venv --list-plugins` flag. Loads every plugin via the same
  discovery path as `run_session` (built-ins + entry points +
  `TN_VENV_PLUGINS`) and prints a table of plugin name, registered
  hooks, and provenance. Hookless plugins (CLI shims, subcommand
  installers, …) appear as `(no hooks)` so they are not silently
  invisible.
- `HookName.HELP_EPILOG` hook. Plugins can now append text to
  `tn-venv --help` output via a public hook instead of monkey-
  patching `argparse`. The listener takes no arguments and returns
  a string; tn-venv appends it after an auto-generated `Plugins:`
  block. Plugins are loaded eagerly by :func:`cli_run` so a plugin
  that wraps ``tn_venv.cli.cli_run`` (e.g. ``tn-venv-gui`` installing
  its ``gui`` subcommand) gets a chance to intercept argv like
  ``tn-venv gui --help`` before argparse sees the help flag.
- `PluginSource` dataclass exported from `tn_venv.plugins`. Every
  loaded plugin instance now carries a `_tn_venv_source` attribute
  describing where it came from (`built-in`,
  `entry-point: NAME=SPEC`, or `env: TN_VENV_PLUGINS`).
  `--list-plugins` consumes this; the attribute is private but the
  dataclass is part of the public API for callers who want to
  introspect plugins programmatically.

### Changed
- The plugin loader is now invoked **eagerly** at the top of
  `tn_venv.cli.cli_run` rather than lazily on `--help`.
  Plugins that replace ``tn_venv.cli.cli_run`` (e.g. the
  ``tn-venv-gui`` package installing its ``gui`` subcommand) now
  intercept argv like ``tn-venv gui --help`` on the very first
  invocation that triggers plugin loading. The lazy ``--help`
  trigger (via ``format_help``) is still in place as a belt-and-
  braces fallback for hosts that print help without going
  through ``cli_run``.
- ``main()`` looks up ``cli_run`` on its own module at call time
  instead of binding the name at definition time, so the
  console-script entry point picks up a plugin's monkey-patch
  even when ``main`` was imported before the patch was applied.

### Fixed
- ``tn-venv gui --help`` printed the full tn-venv help instead of
  the GUI subcommand's help. The cli_run monkey-patch installed
  by the plugin only ran on the *next* invocation, so argparse
  saw ``--help`` first and short-circuited before the wrapper
  could dispatch. Fixed by capturing the original ``cli_run``
  reference at the top of ``cli_run`` and re-invoking through
  the (now-patched) module attribute when the plugin has
  replaced the function.

- Plugin loader dropped every plugin past the first one. A module that
  declared `PLUGINS = [A, B, C]` only ever loaded `A`; the same bug
  affected comma-separated entries in `TN_VENV_PLUGINS` and
  entry points that pointed at modules rather than classes. The
  loader now returns `list[Plugin]` from every code path
  (`_from_module_attr`, `_from_entry_point`, `_resolve_target`) and
  propagates them up to `load_plugins`.
- `vars()` scan picked the first `Plugin` subclass it found, so a
  module that defined `class _Base(Plugin)` followed by
  `class Concrete(_Base)` loaded the empty-named base class instead
  of the concrete one — a silent no-op that hid user plugins.
  The scan now skips classes whose `name` is empty (the convention
  for abstract / helper bases) and exposes a dedicated
  `_scan_module_for_plugins` helper for the rule.
- Replaced `:class:`~tn_venv.…`` cross-references in
  `docs/guide/plugins.md` and `docs/development/architecture.md`
  with plain inline code. The docs site does not configure
  `sphinx.ext.autodoc`, so the references previously rendered as
  literal `<code>~tn_venv.…</code>` (with the leading tilde) and
  provided no linkability.